'use strict';
let fieldName, dealId, settings, requestId;
const button = document.getElementById('launch');
const message = document.getElementById('message');
async function api(path, payload) {
  const auth = BX24.getAuth();
  if (!auth) throw new Error('Авторизация истекла. Откройте карточку заново.');
  const response = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({...payload, member_id:auth.member_id, access_token:auth.access_token})});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Ошибка запроса');
  return data;
}
BX24.init(async () => {
  try {
    const placement = BX24.placement.info();
    const options = placement.options;
    if (options.MODE !== 'view' || options.ENTITY_ID !== 'CRM_DEAL' || !/^[1-9]\d*$/.test(String(options.ENTITY_VALUE_ID))) {
      throw new Error('Запуск доступен в сохранённой карточке сделки.');
    }
    fieldName = options.FIELD_NAME;
    dealId = Number(options.ENTITY_VALUE_ID);
    const data = await api('config', {field_name:fieldName});
    if (data.admin) {
      const edit = document.getElementById('settings');
      edit.hidden = false;
      // Keep the existing authenticated frame: navigation retains BX24 parent context.
      edit.onclick = () => { location.href = 'settings?field=' + encodeURIComponent(fieldName); };
    }
    if (!data.configured) { message.textContent = 'Кнопка ещё не настроена.'; return; }
    if (!data.can_launch) { message.textContent = 'Кнопка выключена.'; return; }
    settings = data.settings;
    button.textContent = settings.label;
    button.style.backgroundColor = settings.color;
    button.style.width = settings.width + 'px';
    button.style.height = settings.height + 'px';
    button.hidden = false;
    message.textContent = '';
    BX24.resizeWindow(document.body.scrollWidth, document.body.scrollHeight);
  } catch (error) { message.textContent = error.message; }
});
button.onclick = async () => {
  if (settings.confirm && !window.confirm('Запустить «' + settings.label + '» для сделки № ' + dealId + '?')) return;
  button.disabled = true;
  // Preserve this key on network error; a manual retry cannot create another workflow.
  requestId ||= crypto.randomUUID();
  try {
    const data = await api('launch', {field_name:fieldName, deal_id:dealId, request_id:requestId});
    if (data.status === 'started' || data.status === 'blocked') {
      message.textContent = 'Процесс запущен: ' + data.workflow_id;
    } else {
      message.textContent = 'Результат требует проверки администратором. Запуск: ' + data.launch_id;
    }
  } catch (error) {
    message.textContent = error.message + '. При потере связи повторите с той же кнопки или проверьте журнал.';
    button.disabled = false;
  }
};
