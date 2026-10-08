async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const txt = () => document.body.innerText;
  for (let i = 0; i < 50 && !/1박당|휴무|선택한 날짜에|객실이 없|no availability/i.test(txt()); i++) await sleep(500);
  await sleep(2000);
  const t = txt();
  if (/휴무입니다|객실이 없습니다|이용 가능한 객실이 없|no availability/i.test(t) && !/1박당/.test(t)) return { count: 0, best: null, closed: true };
  const lines = t.split('\n').map(s => s.trim()).filter(Boolean);
  const offers = [];
  let room = '';
  for (let i = 0; i < lines.length; i++) {
    if (/평방미터$/.test(lines[i])) { room = lines[i - 1] || room; continue; }
    const m = lines[i].match(/^₩([0-9,]+)(?:₩([0-9,]+))?$/);
    if (!m) continue;
    let rate = '', bf = false, refundable = false;
    for (let j = i - 1; j > i - 8 && j > 0; j--) {
      if (lines[j] === '조식 포함') bf = true;
      if (/무료 취소/.test(lines[j])) refundable = true;
      if (/[A-Z]{3,}/.test(lines[j]) && !/^₩/.test(lines[j])) { rate = lines[j]; break; }
    }
    const taxExcluded = /세금 및 수수료 제외/.test(lines.slice(i, i + 4).join(' '));
    offers.push({ room, rate, breakfast: bf, refundable, member: /MEMBER|The1/i.test(rate),
      list: +m[1].replace(/,/g, ''), price: +(m[2] || m[1]).replace(/,/g, ''), taxExcluded });
  }
  const bf = offers.filter(o => o.breakfast).sort((a, b) => a.price - b.price);
  return { count: offers.length, best: bf[0] || null, bestPublic: bf.find(o => !o.member) || null,
    bestFlex: bf.find(o => o.refundable) || null, rates: [...new Set(offers.map(o => o.rate))] };
}
