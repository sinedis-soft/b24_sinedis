"""CRM field wait subscription definitions."""

from app.robots.base import RobotDefinition

WAIT_FIELD_ACTIVITY_CODE = "sinedis.wait_field.activity.v1"
WAIT_FIELD_ROBOT_CODE = "sinedis.wait_field.robot.v1"

PROPERTIES = {
    "entity_type_id": {
        "Name": {"ru": "ID типа CRM-сущности", "en": "CRM entity type ID"},
        "Type": "int",
        "Required": "Y",
        "Multiple": "N",
        "Default": 2,
    },
    "entity_id": {
        "Name": {"ru": "ID элемента", "en": "Entity ID"},
        "Type": "int",
        "Required": "Y",
        "Multiple": "N",
    },
    "field_name": {
        "Name": {"ru": "Код поля", "en": "Field name"},
        "Type": "string",
        "Required": "Y",
        "Multiple": "N",
    },
    "poll_interval_seconds": {
        "Name": {"ru": "Интервал проверки, секунд", "en": "Polling interval, seconds"},
        "Type": "int",
        "Required": "Y",
        "Multiple": "N",
        "Default": 30,
    },
    "timeout_seconds": {
        "Name": {"ru": "Максимальное время ожидания, секунд", "en": "Maximum wait time, seconds"},
        "Type": "int",
        "Required": "Y",
        "Multiple": "N",
        "Default": 86400,
    },
    "error_recipients": {
        "Name": {"ru": "Уведомлять об ошибке", "en": "Notify on error"},  # noqa: RUF001
        "Type": "user",
        "Required": "N",
        "Multiple": "Y",
    },
}
RETURN_PROPERTIES = {
    "status": {"Name": {"ru": "Статус", "en": "Status"}, "Type": "string"},
    "job_id": {"Name": {"ru": "Идентификатор задания", "en": "Job ID"}, "Type": "string"},
    "field_value": {"Name": {"ru": "Значение поля", "en": "Field value"}, "Type": "text"},
    "checks_count": {"Name": {"ru": "Количество проверок", "en": "Checks count"}, "Type": "int"},
    "completed_at": {
        "Name": {"ru": "Условие выполнено", "en": "Condition completed at"},
        "Type": "datetime",
    },
    "error_code": {"Name": {"ru": "Код ошибки", "en": "Error code"}, "Type": "string"},
    "error_message": {"Name": {"ru": "Описание ошибки", "en": "Error message"}, "Type": "text"},
}


def _definition(code: str) -> RobotDefinition:
    return RobotDefinition(
        code=code,
        handler_path="api/bitrix/robots/wait-field",
        name={"ru": "Ожидать заполнения поля CRM", "en": "Wait for CRM field"},
        description={
            "ru": "Продолжает процесс после заполнения поля CRM.",
            "en": "Continues the workflow when a CRM field is populated.",
        },
        properties=PROPERTIES,
        return_properties=RETURN_PROPERTIES,
    )


WAIT_FIELD_ACTIVITY = _definition(WAIT_FIELD_ACTIVITY_CODE)
WAIT_FIELD_ROBOT = _definition(WAIT_FIELD_ROBOT_CODE)
