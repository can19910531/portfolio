/**
 * 團購斷貨退款查詢 — Apps Script 後端
 * 部署方式：網頁應用程式（以我的身分執行、任何人可存取）
 * 資料來源：Google 試算表（網站資料庫）
 *   查詢資料: A會員編號 B姓名 C總金額 D二補加總 E品項數 F斷貨品項數 G應退金額 H明細JSON
 *   退款帳號回覆: A送出時間 B會員編號 C本人姓名(客人填的，僅用於銀行帳號確認) D應退金額 E銀行代碼 F帳號 G狀態 H類型(退款/購物金)
 *   設定: B2月團名稱 B3退款期限 B4開放查詢(是/否)
 */
var SS_ID = 'YOUR_SPREADSHEET_ID'; // ← 換成你自己的 Google 試算表 ID
var SHEET_DATA = '查詢資料';
var SHEET_REPLY = '退款帳號回覆';
var SHEET_CONF = '設定';

function doGet(e) {
  return jsonOut({ ok: false, code: 'method', msg: '請從查詢網站操作' });
}

function doPost(e) {
  var req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return jsonOut({ ok: false, code: 'badreq' });
  }
  try {
    if (req.action === 'query') return handleQuery(req);
    if (req.action === 'submit') return handleSubmit(req);
    return jsonOut({ ok: false, code: 'badreq' });
  } catch (err) {
    return jsonOut({ ok: false, code: 'error', msg: String(err) });
  }
}

function jsonOut(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function getConf() {
  var ss = SpreadsheetApp.openById(SS_ID);
  var v = ss.getSheetByName(SHEET_CONF).getRange('B2:B4').getValues();
  var raw = v[1][0];
  var deadline = '', expired = false;
  var d = null;
  if (raw instanceof Date) {
    d = raw;
  } else {
    var t = String(raw || '').trim().replace(/[.．]/g, '/').replace(/-/g, '/');
    var m = t.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);
    if (m) d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  }
  if (d) {
    deadline = Utilities.formatDate(d, 'Asia/Taipei', 'M月d日');
    // 期限當天整天都還能填；隔天起自動關閉
    var todayStr = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMdd');
    var dlStr = Utilities.formatDate(d, 'Asia/Taipei', 'yyyyMMdd');
    expired = todayStr > dlStr;
  } else {
    deadline = String(raw || '').trim(); // 看不懂的格式：照原樣顯示、不自動關站
  }
  return { month: String(v[0][0] || '').trim(), deadline: deadline, expired: expired,
           open: String(v[2][0] || '').trim() === '是' };
}

function normId(s) {
  var digits = String(s || '').replace(/\D/g, '');
  if (!digits) return '';
  while (digits.length < 4) digits = '0' + digits;
  return 'M' + digits;
}

function findMember(id) {
  var ss = SpreadsheetApp.openById(SS_ID);
  var sh = ss.getSheetByName(SHEET_DATA);
  var last = sh.getLastRow();
  if (last < 2) return null;
  var rows = sh.getRange(2, 1, last - 1, 8).getValues();
  var nid = normId(id);
  if (!nid) return null;
  for (var i = 0; i < rows.length; i++) {
    if (String(rows[i][0]).trim() === nid) {
      return { id: nid, name: String(rows[i][1]).trim(), refund: Number(rows[i][6]) || 0, payloadRaw: rows[i][7] };
    }
  }
  return null;
}

function handleQuery(req) {
  var conf = getConf();
  if (!conf.open) return jsonOut({ ok: false, code: 'closed' });
  if (conf.expired) return jsonOut({ ok: false, code: 'expired', deadline: conf.deadline });
  var m = findMember(req.id);
  if (!m) return jsonOut({ ok: false, code: 'notfound' });
  var payload;
  try {
    payload = JSON.parse(m.payloadRaw);
  } catch (err) {
    return jsonOut({ ok: false, code: 'error', msg: '明細資料格式錯誤，請聯絡主理人' });
  }
  // 查過的會員，告訴她已經送出過什麼（退款帳號 or 轉購物金）
  var prev = latestReply(m.id);
  return jsonOut({ ok: true, month: conf.month, deadline: conf.deadline,
                   member: { id: m.id, name: m.name }, data: payload,
                   submitted: prev ? prev.type : null });
}

function latestReply(id) {
  var ss = SpreadsheetApp.openById(SS_ID);
  var sh = ss.getSheetByName(SHEET_REPLY);
  var last = sh.getLastRow();
  if (last < 2) return null;
  var rows = sh.getRange(2, 1, last - 1, 8).getValues();
  for (var i = rows.length - 1; i >= 0; i--) {
    if (String(rows[i][1]).trim() === id && String(rows[i][6]).trim() === '最新') {
      return { row: i + 2, type: String(rows[i][7] || '退款').trim() || '退款' };
    }
  }
  return null;
}

function handleSubmit(req) {
  var conf = getConf();
  if (!conf.open) return jsonOut({ ok: false, code: 'closed' });
  if (conf.expired) return jsonOut({ ok: false, code: 'expired', deadline: conf.deadline });
  var m = findMember(req.id);
  if (!m) return jsonOut({ ok: false, code: 'notfound' });
  if (!(m.refund > 0)) return jsonOut({ ok: false, code: 'norefund' });
  var isCredit = req.type === 'credit'; // 我要轉購物金：不用填姓名帳號
  var holder = '', bank = '', acct = '';
  if (isCredit) {
    holder = m.name; // 記錄用，帶會員資料裡的姓名
  } else {
    holder = String(req.name || '').normalize('NFKC').trim(); // 本人姓名，僅用於銀行帳號確認
    if (holder.length < 2 || holder.length > 30) return jsonOut({ ok: false, code: 'badname' });
    bank = String(req.bank || '').replace(/\D/g, '');
    acct = String(req.acct || '').replace(/[\s-]/g, '');
    if (!/^\d{3}$/.test(bank)) return jsonOut({ ok: false, code: 'badbank' });
    if (!/^\d{10,14}$/.test(acct)) return jsonOut({ ok: false, code: 'badacct' }); // 台灣銀行帳號通常 10~14 碼
  }

  var lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    var ss = SpreadsheetApp.openById(SS_ID);
    var sh = ss.getSheetByName(SHEET_REPLY);
    // 同一位會員重複送出：舊的標成「已覆蓋」，永遠以最新一筆為準
    var prev = latestReply(m.id);
    if (prev) sh.getRange(prev.row, 7).setValue('已覆蓋');
    var now = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss');
    var row = sh.getLastRow() + 1;
    var rng = sh.getRange(row, 1, 1, 8);
    rng.setNumberFormat('@'); // 全部當文字存，銀行代碼 004 的開頭 0 才不會不見
    rng.setValues([[now, m.id, holder, String(m.refund), bank, acct, '最新', isCredit ? '購物金' : '退款']]);
  } finally {
    lock.releaseLock();
  }
  return jsonOut({ ok: true });
}
