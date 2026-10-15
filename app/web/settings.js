"use strict";
const get = (id) => document.getElementById(id);
let loadedField;
async function api(path, payload) {
  const auth = BX24.getAuth();
  if (!auth)
    throw new Error("Авторизация истекла. Откройте приложение заново.");
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      member_id: auth.member_id,
      access_token: auth.access_token,
    }),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string" ? data.detail : "Ошибка запроса",
    );
  return data;
}
function show(error) {
  get("message").dataset.tone = "error";
  get("message").textContent = error.message;
  WorkflowUI.resize();
}
function preview() {
  WorkflowUI.paint(get("preview"), {
    label: get("label").value || "Запустить бизнес-процесс",
    color: get("color").value,
    width: Number(get("width").value) || 240,
    height: Number(get("height").value) || 40,
  });
  WorkflowUI.resize();
}
async function load() {
  get("form").hidden = true;
  try {
    const field = get("field").value.trim();
    const data = await api("config", { field_name: field });
    if (!data.admin)
      throw new Error("Настройки доступны только администратору.");
    loadedField = field;
    const defaults = {
      label: "Запустить бизнес-процесс",
      template_id: "",
      parameters: {},
      allowed_user_ids: [],
      color: "#2563eb",
      width: 240,
      height: 40,
      enabled: false,
      confirm: true,
      cooldown_seconds: 30,
      block_until_released: false,
    };
    const settings = data.settings || defaults;
    for (const [key, value] of Object.entries(settings)) {
      const input = get(key);
      if (input.type === "checkbox") input.checked = value;
      else
        input.value =
          key === "parameters"
            ? JSON.stringify(value, null, 2)
            : key === "allowed_user_ids"
              ? value.join(", ")
              : value;
    }
    get("form").hidden = false;
    get("message").dataset.tone = "";
    preview();
    get("message").textContent = data.configured
      ? "Настройки загружены."
      : "Настройте новую кнопку.";
    await journal();
  } catch (error) {
    show(error);
  }
}
get("load").onclick = load;
get("form").onsubmit = async (event) => {
  event.preventDefault();
  get("save").disabled = true;
  try {
    const settings = {};
    for (const key of ["label", "color"]) settings[key] = get(key).value;
    for (const key of ["template_id", "width", "height", "cooldown_seconds"])
      settings[key] = Number(get(key).value);
    for (const key of ["enabled", "confirm", "block_until_released"])
      settings[key] = get(key).checked;
    settings.parameters = JSON.parse(get("parameters").value);
    settings.allowed_user_ids = get("allowed_user_ids").value.trim()
      ? get("allowed_user_ids")
          .value.split(",")
          .map((value) => Number(value.trim()))
      : [];
    await api("save", { field_name: loadedField, settings });
    get("message").dataset.tone = "";
    get("message").textContent =
      "Сохранено. Откройте карточку заново, чтобы увидеть изменения.";
  } catch (error) {
    show(error);
  } finally {
    get("save").disabled = false;
    WorkflowUI.resize();
  }
};
async function journal() {
  if (!loadedField) return;
  const data = await api("journal", { field_name: loadedField });
  if (!data.launches.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Запусков пока нет.";
    get("records").replaceChildren(empty);
    WorkflowUI.resize();
    return;
  }
  const table = document.createElement("table");
  const header = table.insertRow();
  for (const text of [
    "Время",
    "Сделка",
    "Сотрудник",
    "Шаблон",
    "Состояние / процесс",
    "Действие",
  ]) {
    const cell = document.createElement("th");
    cell.textContent = text;
    header.appendChild(cell);
  }
  for (const row of data.launches) {
    const tr = table.insertRow();
    for (const text of [
      new Date(row.created_at).toLocaleString(),
      row.deal_id,
      row.user_id,
      row.template_id,
      row.status + " / " + (row.workflow_id || row.launch_id),
    ])
      tr.insertCell().textContent = text;
    const cell = tr.insertCell();
    if (
      !row.released_by &&
      ["starting", "unknown", "blocked"].includes(row.status)
    ) {
      const release = document.createElement("button");
      release.textContent = "Разблокировать";
      release.onclick = async () => {
        if (
          !confirm(
            "Проверьте результат в Битрикс24. Разрешить новый запуск для сделки № " +
              row.deal_id +
              "?",
          )
        )
          return;
        try {
          await api("release", { launch_id: row.launch_id });
          await journal();
        } catch (error) {
          show(error);
        }
      };
      cell.appendChild(release);
    } else
      cell.textContent = row.released_by
        ? "Разблокировал: " + row.released_by
        : "—";
  }
  get("records").replaceChildren(table);
  WorkflowUI.resize();
}
get("journal").onclick = () => journal().catch(show);
BX24.init(() => {
  WorkflowUI.observe();
  for (const key of ["label", "color", "width", "height"])
    get(key).addEventListener("input", preview);
  get("field").value = new URLSearchParams(location.search).get("field") || "";
  if (get("field").value) load();
});

get("back").onclick = () => {
  location.href = "field";
};
