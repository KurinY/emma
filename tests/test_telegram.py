"""Tests for the Telegram adapter utilities."""

from __future__ import annotations

from adapters.telegram import _split_message


def test_short_message_is_returned_as_a_single_chunk():
    assert _split_message("hello") == ["hello"]


def test_empty_message_returns_one_empty_chunk():
    assert _split_message("") == [""]


def test_long_message_is_split_into_multiple_chunks():
    text = "A" * 9000
    chunks = _split_message(text, limit=4000)
    assert len(chunks) > 1
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks) == text


def test_split_prefers_newline_over_hard_cut():
    # The split should fall on the newline, not mid-word.
    text = "A" * 3990 + "\n" + "B" * 100
    chunks = _split_message(text, limit=4000)
    assert chunks[0] == "A" * 3990
    assert chunks[1] == "B" * 100


def test_blank_line_between_paragraphs_is_preserved_across_split():
    # A blank line (\n\n) that falls near the split boundary must survive
    # in the next chunk so paragraph structure is not lost.
    text = "A" * 3990 + "\n\n" + "Second paragraph"
    chunks = _split_message(text, limit=4000)
    assert len(chunks) == 2
    assert chunks[0] == "A" * 3990
    # The blank line must appear at the start of the second chunk.
    assert chunks[1].startswith("\n"), (
        f"blank line lost: second chunk starts with {chunks[1][:20]!r}"
    )
    assert "Second paragraph" in chunks[1]


def test_no_newline_in_first_half_triggers_hard_cut():
    # When rfind finds nothing in the usable window, cut at the hard limit.
    text = "A" * 6000  # no newlines at all
    chunks = _split_message(text, limit=4000)
    assert chunks[0] == "A" * 4000
    assert chunks[1] == "A" * 2000
