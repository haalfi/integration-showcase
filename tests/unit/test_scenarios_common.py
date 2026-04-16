"""Unit tests for pure helpers in scenarios/_common.py (URL builders, parsers, I/O-free)."""

from __future__ import annotations

import pytest
from temporalio.exceptions import ApplicationError

from scenarios._common import (
    build_argparser,
    find_application_error,
    jaeger_search_url,
    jaeger_trace_url,
    parse_trace_id,
    print_links,
    temporal_workflow_url,
)


class TestParseTraceId:
    def test_valid_traceparent_returns_trace_id(self) -> None:
        header = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        assert parse_trace_id(header) == "0af7651916cd43dd8448eb211c80319c"

    def test_empty_string_returns_none(self) -> None:
        assert parse_trace_id("") is None

    @pytest.mark.parametrize(
        "bad_header",
        [
            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331",  # 3 parts
            "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01-extra",  # 5 parts
            "00-tooshort-b7ad6b7169203331-01",  # trace_id wrong length
            "00-0af7651916cd43dd8448eb211c80319z-b7ad6b7169203331-01",  # non-hex
        ],
    )
    def test_malformed_traceparent_returns_none(self, bad_header: str) -> None:
        assert parse_trace_id(bad_header) is None


class TestJaegerUrls:
    def test_trace_url_format(self) -> None:
        url = jaeger_trace_url("0af7651916cd43dd8448eb211c80319c")
        assert url == "http://localhost:16686/trace/0af7651916cd43dd8448eb211c80319c"

    def test_trace_url_strips_trailing_slash(self) -> None:
        url = jaeger_trace_url("abc", base_url="http://jaeger.internal:16686/")
        assert url == "http://jaeger.internal:16686/trace/abc"

    def test_search_url_urlencodes_tag(self) -> None:
        url = jaeger_search_url("tx-001")
        # The JSON tag dict must be URL-encoded so Jaeger parses it correctly.
        assert "tags=%7B%22business_tx_id%22%3A%22tx-001%22%7D" in url
        assert url.startswith("http://localhost:16686/search?service=service-a")


class TestTemporalWorkflowUrl:
    def test_with_run_id_includes_history_path(self) -> None:
        url = temporal_workflow_url("order-abc", "run-xyz")
        assert url == (
            "http://localhost:8088/namespaces/default/workflows/order-abc/run-xyz/history"
        )

    def test_without_run_id_returns_workflow_root(self) -> None:
        url = temporal_workflow_url("order-abc")
        assert url == "http://localhost:8088/namespaces/default/workflows/order-abc"

    def test_urlencodes_workflow_id(self) -> None:
        # workflow_id containing a slash must be escaped so the UI path doesn't break.
        url = temporal_workflow_url("order/with/slash")
        assert "order%2Fwith%2Fslash" in url


class TestFindApplicationError:
    def test_returns_matching_app_error_at_root(self) -> None:
        err = ApplicationError("declined", type="InsufficientFundsError")
        assert find_application_error(err, "InsufficientFundsError") is err

    def test_walks_cause_chain(self) -> None:
        # Mirrors Temporal's WorkflowFailureError wrapping: outer exception
        # exposes the real failure via a ``.cause`` attribute (not __cause__).
        class _WrapperError(Exception):
            def __init__(self, cause: BaseException) -> None:
                super().__init__("wrapped")
                self.cause = cause

        inner = ApplicationError("declined", type="InsufficientFundsError")
        wrapper = _WrapperError(inner)
        assert find_application_error(wrapper, "InsufficientFundsError") is inner

    def test_returns_none_when_type_mismatches(self) -> None:
        err = ApplicationError("other", type="OtherError")
        assert find_application_error(err, "InsufficientFundsError") is None

    def test_returns_none_for_non_application_error(self) -> None:
        assert find_application_error(RuntimeError("boom"), "InsufficientFundsError") is None


class TestPrintLinks:
    def test_emits_jaeger_trace_url_when_trace_id_present(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        print_links(
            business_tx_id="tx-1",
            workflow_id="order-1",
            run_id="run-1",
            trace_id="0af7651916cd43dd8448eb211c80319c",
        )
        out = capsys.readouterr().out
        assert "/trace/0af7651916cd43dd8448eb211c80319c" in out
        assert "order-1/run-1/history" in out

    def test_falls_back_to_search_url_when_trace_id_missing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        print_links(
            business_tx_id="tx-1",
            workflow_id="order-1",
            run_id=None,
            trace_id=None,
        )
        out = capsys.readouterr().out
        assert "/search?service=service-a" in out
        # Without a run_id, the Temporal link drops the /history suffix.
        assert "/workflows/order-1" in out
        assert "/history" not in out


class TestBuildArgparser:
    def test_defaults_used_when_no_args(self) -> None:
        parser = build_argparser(description="test")
        args = parser.parse_args([])
        assert args.items == ["widget-42", "gadget-7"]
        assert args.customer_id == "cust-001"
        assert args.service_a_url == "http://localhost:8000"
        assert args.temporal_address == "localhost:7233"

    def test_override_defaults_via_flags(self) -> None:
        parser = build_argparser(
            description="test",
            default_items=["x"],
            default_customer_id="cust-x",
        )
        args = parser.parse_args(
            [
                "--items",
                "a",
                "b",
                "--customer-id",
                "cust-z",
                "--service-a-url",
                "http://other:9000",
            ]
        )
        assert args.items == ["a", "b"]
        assert args.customer_id == "cust-z"
        assert args.service_a_url == "http://other:9000"
