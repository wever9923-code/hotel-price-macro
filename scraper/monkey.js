async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const main = () => (document.querySelector('main') || document.body).innerText;
  for (let i = 0; i < 40 && !/1박당/.test(main()); i++) await sleep(500);
  // expand every collapsed room so each offer's breakfast/cancel details are in the text
  for (const b of [...document.querySelectorAll('button,a')].filter(b => b.innerText.trim() === '열기')) {
    try { b.click(); await sleep(700); } catch (e) {}
  }
  await sleep(2500);
  const t = main();
  const start = t.indexOf('가격은 세금');
  const endIdx = t.indexOf('기본 정보', start);
  const lines = t.slice(start, endIdx > 0 ? endIdx : undefined).split('\n').map(s => s.trim()).filter(Boolean);
  const offers = [];
  let room = '', seg = [];
  const isRoomTitle = i => /㎡$/.test(lines[i + 1] || '') || /㎡$/.test(lines[i + 2] || '') || /㎡$/.test(lines[i + 3] || '') || /㎡$/.test(lines[i + 4] || '') || /㎡$/.test(lines[i + 5] || '') || /㎡$/.test(lines[i + 6] || '') || /㎡$/.test(lines[i + 7] || '');
  for (let i = 0; i < lines.length; i++) {
    const L = lines[i];
    if (!L.startsWith('[') && !/[()]/.test(L) && !/원$/.test(L) && L.length < 60 && isRoomTitle(i) && !/㎡$/.test(L) && !/^(오션뷰|바다뷰|시티뷰|가든뷰|풀뷰)$/.test(L) && !/층$/.test(L)) {
      room = L.replace(/\s*최저가 객실$/, ''); seg = []; continue;
    }
    if (L === '기타 파트너 상품') { room = '기타 파트너 상품'; seg = []; continue; }
    if (L === '1박당 최저가' && /^[0-9,]+원$/.test(lines[i + 1] || '')) {
      const head = +lines[i + 1].replace(/[^0-9]/g, '');
      let j = i + 2; const det = [];
      while (j < lines.length && lines[j] !== '1박당' && lines[j] !== '열기' && lines[j] !== '예약 불가') { det.push(lines[j]); j++; }
      const ds = det.join('\n');
      const member = lines[j] === '1박당' && lines[j + 1] === '회원 전용 특가';
      offers.push({ room, total: head * 4, perNight: head, breakfast: /조식 포함/.test(ds), cancel: (ds.match(/(\d\d\/\d\d \d+시까지 무료 취소)/) || ds.match(/(환불 불가)/) || [])[1] || '', member, headline: true });
    }
    seg.push(L);
    const m = L.match(/^4박 총액 ([0-9,]+)원$/) || L.match(/^\d+박 총액 ([0-9,]+)원$/);
    if (m) {
      const total = +m[1].replace(/,/g, '');
      const s = seg.join('\n');
      const cancel = (s.match(/(\d\d\/\d\d \d+시까지 무료 취소)/) || s.match(/(환불 불가)/) || [])[1] || '';
      offers.push({ room: room === '기타 파트너 상품' ? (seg.find(x => /^[A-Z0-9 &-]{4,}$/.test(x)) || room) : room, total, perNight: Math.round(total / 4), breakfast: /조식 포함/.test(s), cancel });
      seg = [];
    }
  }
  const bf = offers.filter(o => o.breakfast).sort((a, b) => a.perNight - b.perNight || (a.headline ? 1 : -1));
  return { count: offers.length, best: bf[0] || null, all: bf.slice(0, 5) };
}
