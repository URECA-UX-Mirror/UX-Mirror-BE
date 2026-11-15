"""Playwright 화면에서 클릭 후보 추출 (B1, B2, B4).

후보 규칙 (2026-11-15 결정):
  1. 다른 쇼핑몰 페이지로 이동하는 링크만 (a[href^="/shop/"])
  2. 첫 화면(뷰포트, 스크롤 없음)에 보이는 것만
  3. 현재 페이지로 가는 링크 제외
  4. 위험 동작 제외 (회원 탈퇴, 로그아웃, 접수/결제/삭제 등)
  5. 상단 공통 메뉴(헤더: 로고·메뉴)는 시작 화면(홈)에서만 후보 (2026-11-15, 팀 합의 필요)
     근거: B5 orders에서 상단 메뉴가 있으면 '환불 신청하기' 0.119(4bit)/0.223(bf16), 빼면 0.859/0.928.
     허용 튜닝(질문 문구·후보 형식·페르소나·이력)으로는 0.25를 못 넘음. 홈은 그대로라 라벨 비교 실험에 영향 없음.
제외된 클릭 요소는 exclusionReasons로 남긴다.

semanticKey는 백엔드 전용. 라벨이 아니라 영역·목적지·DOM 순서로 만들어 Before/After에서 같은 값이 나온다.
"""
from urllib.parse import urlsplit

VIEWPORT = {"width": 1440, "height": 900}
START_PATH = "/shop/home"          # 상단 공통 메뉴를 후보로 넣는 유일한 화면
GLOBAL_NAV_REGION = "상단 메뉴"

DANGEROUS_WORDS = ("회원 탈퇴", "회원탈퇴", "로그아웃", "접수하기", "결제하기", "삭제", "탈퇴")

_JS = r"""
(args) => {
  const [vw, vh, dangerous] = args;
  const isIcon = (n) => n && n.closest && n.closest('.material-symbols-outlined');
  const isBreadcrumb = (el) => {
    if (el.closest('nav[aria-label="Breadcrumb" i], [aria-label="breadcrumb" i]')) return true;
    const p = el.parentElement;   // 링크 옆에 '›' 아이콘이 붙은 줄
    return !!p && [...p.children].some(c => c !== el && c.matches('.material-symbols-outlined') &&
      /^(chevron_right|navigate_next)$/.test(c.textContent.trim()));
  };
  const regionOf = (el) => {
    if (el.closest('header')) return '상단 메뉴';
    if (el.closest('footer')) return '하단';
    if (isBreadcrumb(el)) return '경로 표시';
    if (el.closest('aside')) return '사이드 메뉴';
    return '본문';
  };
  // 첫 화면 안에 실제로 보이는 글자만 라벨로 쓴다 (화면 밖으로 잘린 카드 아래쪽 문구 제외). 아이콘 글자는 제외.
  const labelOf = (el) => {
    const parts = [];
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const range = document.createRange();
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = n.textContent.replace(/\s+/g, ' ').trim();
      if (!t || isIcon(n.parentElement)) continue;
      range.selectNodeContents(n);
      const r = range.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;               // 숨은 글자
      if (r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;   // 화면 밖
      parts.push(t);
    }
    return parts.join(' ').replace(/\s+/g, ' ').trim();
  };
  // 본문 버튼이 속한 작은 묶음(카드 등)의 제목. 위로 올라가며 버튼 바깥의 h1~h4를 찾되,
  // 묶음이 화면 절반보다 커지면(페이지 전체 섹션) 멈춘다. 상단 메뉴·하단·경로 표시는 맥락 없음.
  const contextOf = (el, region) => {
    if (region !== '본문' && region !== '사이드 메뉴') return null;
    let a = el.parentElement;
    for (let depth = 0; a && depth < 8 && a.tagName !== 'MAIN' && a.tagName !== 'BODY'; depth++, a = a.parentElement) {
      if (a.getBoundingClientRect().height > vh * 0.5) break;
      // 다른 링크/버튼 안의 제목(옆 카드 제목)은 맥락이 아니다
      const h = [...a.querySelectorAll('h1,h2,h3,h4')].find(x => !el.contains(x) && !x.closest('a,button') &&
                                                            x.innerText.trim());
      if (h) return h.innerText.replace(/\s+/g, ' ').trim();
    }
    return null;
  };
  const labelOfAll = (el) => {   // 제외 목록 기록용 (화면 밖 요소도 이름은 남긴다)
    const c = el.cloneNode(true);
    c.querySelectorAll('.material-symbols-outlined').forEach(s => s.remove());
    return (c.textContent || '').replace(/\s+/g, ' ').trim();
  };
  const here = location.pathname.replace(/\/$/, '');
  const out = [];
  document.querySelectorAll('a, button, [role=button], input[type=submit]').forEach((el, domIndex) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const label = labelOf(el);
    const href = el.tagName === 'A' ? el.getAttribute('href') : null;
    let reason = null;
    if (r.width === 0 || r.height === 0 || cs.visibility === 'hidden' || cs.display === 'none') reason = 'HIDDEN';
    else if (dangerous.some(w => (el.innerText || '').includes(w))) reason = 'DANGEROUS_ACTION';
    else if (!href || !href.startsWith('/shop/')) reason = 'NOT_NAVIGATION';
    else if (href.replace(/\/$/, '') === here) reason = 'SELF_LINK';
    else if (r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) reason = 'OUT_OF_VIEWPORT';
    if (!reason && !label) reason = 'NO_VISIBLE_LABEL';
    const region = regionOf(el);
    out.push({domIndex, tag: el.tagName.toLowerCase(), label: label || labelOfAll(el), href, region, reason,
              context: reason ? null : contextOf(el, region),
              bbox: {x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height)}});
  });
  return out;
}
"""


def extract(page) -> dict:
    """현재 페이지에서 후보와 제외 목록을 뽑는다. 페이지는 수정하지 않는다."""
    vp = page.viewport_size
    raw = page.evaluate(_JS, [vp["width"], vp["height"], list(DANGEROUS_WORDS)])
    path = urlsplit(page.url).path.rstrip("/")
    candidates, excluded, seen = [], [], {}
    for el in raw:
        if not el["reason"] and el["region"] == GLOBAL_NAV_REGION and path != START_PATH:
            el["reason"] = "GLOBAL_NAV_NOT_START"
        if el["reason"]:
            if el["reason"] != "HIDDEN" and el["label"]:
                excluded.append({"label": el["label"], "region": el["region"], "reason": el["reason"]})
            continue
        base = f'{el["region"]}>{el["href"]}'
        seen[base] = seen.get(base, 0) + 1
        candidates.append({
            "optionId": f"option_{len(candidates) + 1:02d}",
            "label": el["label"],
            "region": el["region"],
            "context": el["context"],
            "href": el["href"],
            "bbox": el["bbox"],
            "semanticKey": f"{base}#{seen[base]}",   # 백엔드 전용
        })
    return {"url": page.url, "title": page.title(), "candidates": candidates, "excluded": excluded}


def draw_probabilities(page, candidates, probs: dict, chosen: str | None):
    """B6 미리보기: 각 후보 위에 모델 확률 배지. 선택된 후보는 초록 굵은 테두리."""
    page.evaluate(r"""
    ([cands, probs, chosen]) => {
      document.querySelectorAll('.uxm-overlay').forEach(e => e.remove());
      // bbox는 창 기준 좌표 → 페이지 기준(absolute)으로 그려 스크롤해도 버튼을 따라가게 한다
      const sx = scrollX, sy = scrollY;
      cands.forEach(c => {
        const p = probs[c.optionId] ?? 0, sel = c.optionId === chosen;
        const color = sel ? '#059669' : '#e11d48';
        const x = c.bbox.x + sx, y = c.bbox.y + sy;
        const b = document.createElement('div');
        b.className = 'uxm-overlay';
        Object.assign(b.style, {position: 'absolute', left: x - 3 + 'px', top: y - 3 + 'px',
          width: c.bbox.width + 6 + 'px', height: c.bbox.height + 6 + 'px', border: `${sel ? 4 : 2}px solid ${color}`,
          borderRadius: '6px', background: `rgba(225,29,72,${(p * 0.35).toFixed(3)})`, zIndex: 2147483646,
          pointerEvents: 'none'});
        const n = document.createElement('div');
        n.className = 'uxm-overlay';
        n.textContent = (p * 100).toFixed(1) + '%' + (sel ? ' ✓' : '');
        Object.assign(n.style, {position: 'absolute', left: x + 'px', top: y + c.bbox.height + 4 + 'px',
          background: color, color: '#fff', font: 'bold 13px sans-serif', padding: '2px 7px',
          borderRadius: '10px', zIndex: 2147483647, pointerEvents: 'none', whiteSpace: 'nowrap'});
        if (c.bbox.y + c.bbox.height + 26 > innerHeight) n.style.top = (y - 22) + 'px';   // 첫 화면 아래 끝이면 위에 표시
        document.body.append(b, n);
      });
    }""", [candidates, probs, chosen])


def draw_overlay(page, candidates, color="#e11d48"):
    """시각 확인용. 추출이 끝난 뒤에만 호출한다 (오버레이가 모델 입력에 섞이지 않게)."""
    page.evaluate(r"""
    ([cands, color]) => {
      document.querySelectorAll('.uxm-overlay').forEach(e => e.remove());
      const sx = scrollX, sy = scrollY;   // 페이지 기준으로 그려 스크롤해도 버튼을 따라가게
      cands.forEach((c, i) => {
        const x = c.bbox.x + sx, y = c.bbox.y + sy;
        const b = document.createElement('div');
        b.className = 'uxm-overlay';
        Object.assign(b.style, {position: 'absolute', left: x - 3 + 'px', top: y - 3 + 'px',
          width: c.bbox.width + 6 + 'px', height: c.bbox.height + 6 + 'px', border: `3px solid ${color}`,
          borderRadius: '6px', zIndex: 2147483646, pointerEvents: 'none'});
        const n = document.createElement('div');
        n.className = 'uxm-overlay';
        n.textContent = String(i + 1);
        Object.assign(n.style, {position: 'absolute', left: x - 12 + 'px', top: Math.max(0, y - 14) + 'px',
          background: color, color: '#fff', font: 'bold 13px sans-serif', padding: '2px 7px',
          borderRadius: '10px', zIndex: 2147483647, pointerEvents: 'none'});
        document.body.append(b, n);
      });
    }""", [candidates, color])
