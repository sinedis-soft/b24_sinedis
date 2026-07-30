"""Universal subscription REST request definitions."""

from app.robots.base import RobotDefinition

REST_REQUEST_ACTIVITY_CODE = "sinedis.rest_request.activity.v1"
REST_REQUEST_ROBOT_CODE = "sinedis.rest_request.robot.v1"

PROPERTIES = {
    "rest_method": {
        "Name": {"ru": "REST-метод", "en": "REST method"},
        "Type": "string",
        "Required": "Y",
        "Multiple": "N",
    },
    "request_params_json": {
        "Name": {"ru": "Параметры REST-запроса (JSON)", "en": "REST parameters (JSON)"},
        "Type": "text",
        "Required": "Y",
        "Multiple": "N",
        "Default": "{}",
    },
    "jsonpath": {
        "Name": {"ru": "JSONPath", "en": "JSONPath"},
        "Type": "string",
        "Required": "Y",
        "Multiple": "N",
        "Default": "$",
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
    "result_text": {"Name": {"ru": "Результат", "en": "Result"}, "Type": "text"},
    "result_json": {"Name": {"ru": "Результат JSON", "en": "JSON result"}, "Type": "text"},
    "matches_count": {
        "Name": {"ru": "Количество совпадений", "en": "Matches count"},
        "Type": "int",
    },
    "error_code": {"Name": {"ru": "Код ошибки", "en": "Error code"}, "Type": "string"},
    "error_message": {"Name": {"ru": "Описание ошибки", "en": "Error message"}, "Type": "text"},
}


def _definition(code: str) -> RobotDefinition:
    return RobotDefinition(
        code=code,
        handler_path="api/bitrix/robots/rest-request",
        name={"ru": "REST-запрос", "en": "REST request"},
        description={
            "ru": "Выполняет REST-метод и выбирает результат по JSONPath.",
            "en": "Calls a REST method and selects its result with JSONPath.",
        },
        properties=PROPERTIES,
        return_properties=RETURN_PROPERTIES,
    )


REST_REQUEST_ACTIVITY = _definition(REST_REQUEST_ACTIVITY_CODE)
REST_REQUEST_ROBOT = _definition(REST_REQUEST_ROBOT_CODE)
