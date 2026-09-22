"""Turns raw DOM state into the semantic signals the rest of the system needs:
ranked locator candidates for a clicked point, and a bounded element scan for
observation. This is the one place that reaches into the page with raw JS --
everything above this module only ever sees LocatorCandidate/ElementSummary.
"""

from cua.core.models import LocatorCandidate, LocatorStrategy

_ACTIVE_ELEMENT_IS_PASSWORD_JS = """
() => {
  function isPassword(el) {
    return !!el && el.tagName && el.tagName.toLowerCase() === 'input'
      && (el.getAttribute('type') || '').toLowerCase() === 'password';
  }
  let el = document.activeElement;
  let depth = 0;
  while (el && el.tagName && el.tagName.toLowerCase() === 'iframe' && depth < 3) {
    try {
      el = el.contentDocument ? el.contentDocument.activeElement : null;
    } catch (e) {
      el = null;
    }
    depth++;
  }
  return isPassword(el);
}
"""

_DESCRIBE_POINT_JS = """
([x, y]) => {
  const el = document.elementFromPoint(x, y);
  if (!el) return null;
  function ariaLabel(node) { return node.getAttribute && node.getAttribute('aria-label'); }
  function labelText(node) {
    if (node.labels && node.labels.length) return node.labels[0].textContent.trim();
    if (node.id) {
      const lbl = document.querySelector(`label[for="${node.id}"]`);
      if (lbl) return lbl.textContent.trim();
    }
    return null;
  }
  function roleOf(node) {
    const explicit = node.getAttribute && node.getAttribute('role');
    if (explicit) return explicit;
    const tag = node.tagName.toLowerCase();
    if (tag === 'a' && node.href) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const t = (node.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      if (t === 'checkbox') return 'checkbox';
      return 'textbox';
    }
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    return null;
  }
  function tablePos(node) {
    const cell = node.closest('td,th');
    if (!cell) return null;
    const row = cell.parentElement;
    const table = cell.closest('table');
    if (!row || !table) return null;
    const col = Array.prototype.indexOf.call(row.children, cell);
    const rowIdx = Array.prototype.indexOf.call(table.querySelectorAll('tr'), row);
    return {row: rowIdx, col: col};
  }
  function cssPath(node) {
    const parts = [];
    let cur = node;
    for (let i = 0; i < 5 && cur && cur.nodeType === 1; i++) {
      let part = cur.tagName.toLowerCase();
      if (cur.parentElement) {
        const idx = Array.prototype.indexOf.call(cur.parentElement.children, cur) + 1;
        part += ':nth-child(' + idx + ')';
      }
      parts.unshift(part);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  }
  function safeText(node) {
    const isPw = node.tagName.toLowerCase() === 'input' && (node.getAttribute('type') || '').toLowerCase() === 'password';
    if (isPw) return '';
    return (node.innerText || node.value || '').trim();
  }
  const formEl = el.closest('form');
  return {
    tag: el.tagName.toLowerCase(),
    role: roleOf(el),
    aria_label: ariaLabel(el),
    label_text: labelText(el),
    text: safeText(el).slice(0, 80),
    table: tablePos(el),
    css: cssPath(el),
    form_action: formEl ? (formEl.getAttribute('action') || '') : null,
    form_method: formEl ? (formEl.getAttribute('method') || 'get').toUpperCase() : null,
    href: (el.tagName.toLowerCase() === 'a') ? el.getAttribute('href') : null,
  };
}
"""

_IFRAME_HIT_JS = """
([x, y]) => {
  const el = document.elementFromPoint(x, y);
  if (!el || el.tagName.toLowerCase() !== 'iframe') return null;
  const r = el.getBoundingClientRect();
  return { name: el.getAttribute('name'), left: r.left, top: r.top };
}
"""

_SCAN_JS = """
() => {
  const sel = 'a,button,input,select,textarea,td,th';
  const nodes = Array.from(document.querySelectorAll(sel)).slice(0, 120);
  function roleOf(node) {
    const tag = node.tagName.toLowerCase();
    const explicit = node.getAttribute('role');
    if (explicit) return explicit;
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const t = (node.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      return 'textbox';
    }
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    return 'cell';
  }
  function safeText(node) {
    const isPw = node.tagName.toLowerCase() === 'input' && (node.getAttribute('type') || '').toLowerCase() === 'password';
    if (isPw) return '';
    return (node.innerText || node.value || '').trim();
  }
  return nodes
    .filter(n => n.offsetParent !== null)
    .map(n => ({
      tag: n.tagName.toLowerCase(),
      role: roleOf(n),
      name: n.getAttribute('aria-label') || null,
      text: safeText(n).slice(0, 60),
    }))
    .filter(e => e.text || e.name)
    .slice(0, 80);
}
"""

_SCAN_DESCRIBE_JS = """
() => {
  const sel = 'a,button,input,select,textarea';
  function ariaLabel(node) { return node.getAttribute && node.getAttribute('aria-label'); }
  function labelText(node) {
    if (node.labels && node.labels.length) return node.labels[0].textContent.trim();
    if (node.id) {
      const lbl = document.querySelector(`label[for="${node.id}"]`);
      if (lbl) return lbl.textContent.trim();
    }
    return null;
  }
  function roleOf(node) {
    const explicit = node.getAttribute && node.getAttribute('role');
    if (explicit) return explicit;
    const tag = node.tagName.toLowerCase();
    if (tag === 'a' && node.href) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') {
      const t = (node.getAttribute('type') || 'text').toLowerCase();
      if (t === 'submit' || t === 'button') return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'hidden') return null;
      return 'textbox';
    }
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    return null;
  }
  function isPassword(node) {
    return node.tagName.toLowerCase() === 'input' && (node.getAttribute('type') || '').toLowerCase() === 'password';
  }
  function tablePos(node) {
    const cell = node.closest('td,th');
    if (!cell) return null;
    const row = cell.parentElement;
    const table = cell.closest('table');
    if (!row || !table) return null;
    const col = Array.prototype.indexOf.call(row.children, cell);
    const rowIdx = Array.prototype.indexOf.call(table.querySelectorAll('tr'), row);
    return {row: rowIdx, col: col};
  }
  function cssPath(node) {
    const parts = [];
    let cur = node;
    for (let i = 0; i < 5 && cur && cur.nodeType === 1; i++) {
      let part = cur.tagName.toLowerCase();
      if (cur.parentElement) {
        const idx = Array.prototype.indexOf.call(cur.parentElement.children, cur) + 1;
        part += ':nth-child(' + idx + ')';
      }
      parts.unshift(part);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  }
  const nodes = Array.from(document.querySelectorAll(sel)).slice(0, 150);
  return nodes
    .filter(n => n.offsetParent !== null)
    .map(n => ({
      tag: n.tagName.toLowerCase(),
      role: roleOf(n),
      aria_label: ariaLabel(n),
      label_text: labelText(n),
      text: (isPassword(n) ? '' : (n.innerText || n.value || '')).trim().slice(0, 80),
      table: tablePos(n),
      css: cssPath(n),
      is_password: isPassword(n),
    }))
    .filter(e => e.role)
    .slice(0, 60);
}
"""

_ELEMENT_LINK_INFO_JS = """
(el) => {
  const formEl = el.closest('form');
  return {
    url: (el.tagName.toLowerCase() === 'a') ? el.getAttribute('href') : (formEl ? formEl.getAttribute('action') : null),
    method: formEl ? (formEl.getAttribute('method') || 'get').toUpperCase() : null,
  };
}
"""


def describe_point(page, x: int, y: int) -> dict | None:
    """Hit-tests a viewport coordinate and describes what's there, following
    one level into a named iframe if the point lands on one (our app never
    nests iframes deeper than that).
    """
    top = page.evaluate(_DESCRIBE_POINT_JS, [x, y])
    if top is None:
        return None
    if top["tag"] != "iframe":
        return {**top, "frame": None}
    rect = page.evaluate(_IFRAME_HIT_JS, [x, y])
    if not rect or not rect.get("name"):
        return {**top, "frame": None}
    frame = page.frame(name=rect["name"])
    if frame is None:
        return {**top, "frame": None}
    inner = frame.evaluate(_DESCRIBE_POINT_JS, [x - rect["left"], y - rect["top"]])
    if inner is None:
        return {**top, "frame": rect["name"]}
    inner["frame"] = rect["name"]
    return inner


def candidates_from_description(desc: dict) -> list[LocatorCandidate]:
    """Ranks locator candidates for a described element, most robust first."""
    candidates: list[LocatorCandidate] = []
    accessible_name = desc.get("aria_label") or desc.get("label_text") or desc.get("text") or None
    if desc.get("role") and accessible_name:
        candidates.append(
            LocatorCandidate(strategy=LocatorStrategy.ROLE, role=desc["role"], value=accessible_name)
        )
    if desc.get("label_text"):
        candidates.append(LocatorCandidate(strategy=LocatorStrategy.LABEL, value=desc["label_text"]))
    if desc.get("text"):
        candidates.append(LocatorCandidate(strategy=LocatorStrategy.TEXT, value=desc["text"]))
    table = desc.get("table")
    if table:
        candidates.append(
            LocatorCandidate(
                strategy=LocatorStrategy.TABLE_POSITION,
                value=f"row={table['row']},col={table['col']}",
            )
        )
    if desc.get("css"):
        candidates.append(LocatorCandidate(strategy=LocatorStrategy.CSS, value=desc["css"]))
    return candidates


def scan_frame(scope) -> list[dict]:
    return scope.evaluate(_SCAN_JS)


def scan_frame_described(scope) -> list[dict]:
    """Like scan_frame, but returns the fuller descriptor (role/aria_label/
    label_text/text/table/css) that candidates_from_description() needs --
    used by read_page/find so every ref they hand out already carries a
    ranked locator, not just a role+name summary.
    """
    return scope.evaluate(_SCAN_DESCRIBE_JS)


def link_info(locator) -> dict:
    """For an already-resolved semantic-target locator: where would clicking
    it actually go? Used to predict a URL/method for the guardrail check
    before the click happens.
    """
    return locator.evaluate(_ELEMENT_LINK_INFO_JS)


def focused_element_is_password(page) -> bool:
    """Checks the TRUE current focus (document.activeElement, descending
    into a focused same-origin iframe if needed), not an inferred one.

    This backs redaction for the `type` action, which the browser toolset
    sends with no target of its own -- it types wherever focus already is.
    Tracking focus by "what was last clicked" is not reliable (a Tab key
    press, or the model retyping after a mistake, moves focus without a
    click), so this checks the DOM directly at the moment it matters instead
    of trusting our own click history.
    """
    return bool(page.evaluate(_ACTIVE_ELEMENT_IS_PASSWORD_JS))
