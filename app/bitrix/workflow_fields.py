"""Register the application-owned CRM field type without creating duplicate fields."""

from app.bitrix.client import BitrixClient

USER_TYPE_ID = "sinedis_bp_button"


async def ensure_workflow_field_type(client: BitrixClient, handler: str) -> None:
    response = await client.call("userfieldtype.list")
    existing = response.result
    if not isinstance(existing, list):
        raise ValueError("Unexpected userfieldtype.list response")
    found = any(item.get("USER_TYPE_ID") == USER_TYPE_ID for item in existing)
    await client.call(
        "userfieldtype.update" if found else "userfieldtype.add",
        {
            "USER_TYPE_ID": USER_TYPE_ID,
            "HANDLER": handler,
            "TITLE": "Запуск бизнес-процесса SINEDIS",
            "DESCRIPTION": "Настраиваемая кнопка запуска процесса в сделке",
            "OPTIONS": {"height": 90},
        },
    )
