from types import SimpleNamespace

import pytest

from app.bot import handlers


def card_press(data: str = "sunrise") -> SimpleNamespace:
    return SimpleNamespace(
        data=data,
        bot=object(),
        message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=7),
        from_user=SimpleNamespace(id=1),
    )


@pytest.fixture
def calls(monkeypatch) -> list[str]:
    recorded: list[str] = []

    async def answer(callback, text=None):
        recorded.append("answer")

    monkeypatch.setattr(handlers, "answer_callback", answer)
    return recorded


@pytest.mark.asyncio
async def test_the_button_is_answered_only_after_the_card_is_in_place(monkeypatch, calls):
    async def send_card(*args, **kwargs):
        calls.append("card")

    monkeypatch.setattr(handlers, "send_card", send_card)

    await handlers.card_callback(card_press())

    assert calls == ["card", "answer"]


@pytest.mark.asyncio
async def test_a_failed_card_still_answers_the_button(monkeypatch, calls):
    async def send_card(*args, **kwargs):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(handlers, "send_card", send_card)

    with pytest.raises(RuntimeError):
        await handlers.card_callback(card_press())

    assert calls == ["answer"]
