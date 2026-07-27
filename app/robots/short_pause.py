"""Definition of the subscription-based short-pause robot."""

from app.robots.base import RobotDefinition

SHORT_PAUSE_CODE = "sinedis.short_pause.v1"

SHORT_PAUSE_ROBOT = RobotDefinition(
    code=SHORT_PAUSE_CODE,
    handler_path="api/bitrix/robots/short-pause",
    name={"ru": "Короткая пауза", "en": "Short pause"},
    description={
        "ru": (
            "Приостанавливает выполнение автоматизации или бизнес-процесса "
            "на указанное количество секунд."
        ),
        "en": "Pauses an automation rule or workflow for the specified number of seconds.",
    },
    properties={
        "delay_seconds": {
            "Name": {"ru": "Продолжительность, секунд", "en": "Duration, seconds"},
            "Description": {
                "ru": "Время ожидания перед продолжением процесса.",
                "en": "Time to wait before continuing the process.",
            },
            "Type": "int",
            "Required": "Y",
            "Multiple": "N",
            "Default": 10,
        },
        "comment": {
            "Name": {"ru": "Комментарий", "en": "Comment"},
            "Description": {
                "ru": "Необязательное описание причины паузы.",
                "en": "Optional description of the pause.",
            },
            "Type": "string",
            "Required": "N",
            "Multiple": "N",
            "Default": "",
        },
    },
    return_properties={
        "status": {"Name": {"ru": "Статус", "en": "Status"}, "Type": "string"},
        "job_id": {
            "Name": {"ru": "Идентификатор задания", "en": "Job ID"},
            "Type": "string",
        },
        "scheduled_at": {
            "Name": {"ru": "Запланировано", "en": "Scheduled at"},
            "Type": "datetime",
        },
        "resumed_at": {
            "Name": {"ru": "Продолжено", "en": "Resumed at"},
            "Type": "datetime",
        },
        "requested_delay_seconds": {
            "Name": {"ru": "Запрошенная пауза", "en": "Requested delay"},
            "Type": "int",
        },
        "actual_delay_seconds": {
            "Name": {"ru": "Фактическая пауза", "en": "Actual delay"},
            "Type": "int",
        },
    },
)
