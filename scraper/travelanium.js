async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const txt = () => document.body.innerText;
  for (let i = 0; i < 40 && !/including taxes|no rooms? available|not available for/i.test(txt()); i++) await sleep(500);
  await sleep(1500);
  const lines = txt().split('\n').map(s => s.trim()).filter(Boolean);
  const offers = [];
  let room = '';
  for (let i = 0; i < lines.length; i++) {
    const L = lines[i];
    if (/^\d+$/.test(lines[i + 1] || '') && [2, 3, 4, 5].some(d => /m²/.test(lines[i + d] || '')) && !/KRW|THB|m²/.test(L)) { room = L; continue; }
    if (/^you will get/i.test(L)) {
      const rate = lines[i - 1] || '';
      let j = i + 1; const perks = [];
      while (j < lines.length && !/^(KRW|THB|USD) [0-9,.]+$/.test(lines[j]) && j < i + 15) { perks.push(lines[j]); j++; }
      const m = (lines[j] || '').match(/^(KRW|THB|USD) ([0-9,.]+)$/);
      if (!m) continue;
      const k = lines.findIndex((x, n) => n >= j && /^booking condition$/i.test(x));
      const cond = k > 0 && k < j + 4 ? (lines[k + 1] || '') : '';
      const fc = (cond.match(/[Ff]ree cancellation[^.]*until (\d{1,2} \w{3} \d{4})/) || [])[1] || '';
      offers.push({ room, rate, currency: m[1], perNight: Math.round(+m[2].replace(/,/g, '')),
        breakfast: perks.some(p => /breakfast included/i.test(p)), freeCancel: fc,
        refundable: /free cancellation/i.test(perks.join(' ')) && !/^non[- ]refundable/i.test(rate),
        perks: perks.filter(p => !/^(Wireless|Access to facilities|free cancellation)/i.test(p)).slice(0, 4), cond: cond.slice(0, 300) });
    }
  }
  const bf = offers.filter(o => o.breakfast).sort((a, b) => a.perNight - b.perNight);
  const promos = [...new Set(offers.map(o => o.rate.replace(/\s*-\s*[a-z &]*beds?$/i, '').trim()).filter(r => r && !/^(non[- ]refundable|flexible|room only)$/i.test(r)))];
  return { count: offers.length, best: bf[0] || null, bestFlex: bf.find(o => o.refundable) || null, promos, sample: bf.slice(0, 3) };
}
