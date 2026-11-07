const el = (id) => document.getElementById(id);

async function loadRateSheet() {
  const res = await fetch('/api/rate-sheet');
  const rows = await res.json();
  el('rate-sheet-count').textContent = `(${rows.length})`;
  const table = el('rate-table');
  table.innerHTML = rows
    .map(
      (r) => `
      <tr>
        <td>
          <span class="rt-name">${r.name}</span>
          <span class="rt-meta">${r.city} · ${r.room_type}</span>
        </td>
        <td class="rt-price">$${r.price_per_night.toFixed(0)}/night</td>
      </tr>`
    )
    .join('');
}

async function loadSamples() {
  const res = await fetch('/api/sample-inquiries');
  const samples = await res.json();
  const picker = el('sample-picker');
  picker.innerHTML = samples
    .map((s) => `<button class="sample-chip" data-id="${s.id}" data-text="${encodeURIComponent(s.text)}">${s.id.replace(/_/g, ' ')}</button>`)
    .join('');
  picker.querySelectorAll('.sample-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      el('inquiry-text').value = decodeURIComponent(chip.dataset.text);
    });
  });
}

function setStage(stage) {
  const order = ['parsed', 'clarify', 'priced'];
  const idx = order.indexOf(stage);
  document.querySelectorAll('.stage').forEach((node) => {
    const nodeIdx = order.indexOf(node.dataset.stage);
    node.classList.remove('is-active', 'is-done');
    if (nodeIdx < idx) node.classList.add('is-done');
    if (nodeIdx === idx) node.classList.add('is-active');
  });
}

function money(v) {
  return v === null || v === undefined ? 'N/A' : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function nightsLabel(leg) {
  if (leg.nights !== null && leg.nights !== undefined) return `${leg.nights} nights`;
  if (leg.nights_options && leg.nights_options.length) return `${leg.nights_options.join(' or ')} nights (unconfirmed)`;
  return '? nights';
}

function renderBrief(brief, audit) {
  const grid = el('brief-grid');
  const legsText = brief.legs
    .map((l) => `${l.destination} (${nightsLabel(l)}, ${l.travellers_count ?? '?'} travellers)`)
    .join('; ');
  const rows = [
    ['Legs', legsText],
    ['Dates', brief.dates_or_window || 'N/A'],
    ['Budget', brief.budget_usd ? money(brief.budget_usd) : 'N/A'],
    ['Preferences', brief.preferences.join(', ') || '—'],
    ['Hard constraints', brief.hard_constraints.join(', ') || '—'],
  ];
  grid.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');

  const notes = audit.legs.flatMap((l) => l.notes);
  const notesEl = el('brief-notes');
  if (notes.length) {
    notesEl.hidden = false;
    notesEl.innerHTML = notes.map((n) => `<li>${n}</li>`).join('');
  } else {
    notesEl.hidden = true;
  }

  el('brief-block').hidden = false;
}

function renderQuestions(cq) {
  const list = el('questions-list');
  if (!cq.questions.length) {
    list.className = 'questions is-empty';
    list.innerHTML = '<li>Nothing missing or unclear.</li>';
  } else {
    list.className = 'questions';
    list.innerHTML = cq.questions.map((q) => `<li>${q}</li>`).join('');
  }
  el('questions-block').hidden = false;
}

function renderProposal(proposal) {
  const legsEl = el('itinerary-legs');
  legsEl.innerHTML = proposal.legs
    .map((leg) => {
      if (leg.status === 'priced' && leg.line_item) {
        const li = leg.line_item;
        return `
          <div class="leg">
            <div class="leg__title">${leg.destination} — ${leg.hotel_name}</div>
            <div class="leg__row">
              <span>${li.nights} nights${li.room_count > 1 ? ` × ${li.room_count} rooms` : ''} × ${money(li.rate_per_night)}</span>
              <span class="leg__amount">${money(li.subtotal)}</span>
            </div>
            ${leg.budget_exceeded ? '<div class="leg__flag">Exceeds the client’s stated budget</div>' : ''}
            ${leg.assumed_fields && leg.assumed_fields.length ? `<div class="leg__flag">Assumed: ${leg.assumed_fields.join(', ')} (not confirmed by client)</div>` : ''}
          </div>`;
      }
      return `
        <div class="leg">
          <div class="leg__title">${leg.destination} — to be quoted</div>
          <div class="leg__reason">${leg.unmet_reasons.join('; ')}</div>
        </div>`;
    })
    .join('');

  const ledger = el('ledger');
  if (proposal.total_price_usd !== null && proposal.total_price_usd !== undefined) {
    ledger.hidden = false;
    ledger.innerHTML = `<span>Total</span><span class="amount">${money(proposal.total_price_usd)}</span>`;
  } else if (proposal.priced_subtotal_usd !== null && proposal.priced_subtotal_usd !== undefined) {
    // "partial" status: some legs priced, some to_be_quoted -- never label this "Total".
    ledger.hidden = false;
    ledger.innerHTML = `<span>Priced legs only (incomplete trip)</span><span class="amount">${money(proposal.priced_subtotal_usd)}</span>`;
  } else {
    ledger.hidden = true;
  }
  el('itinerary-block').hidden = false;

  if (proposal.narration) {
    el('narration-text').textContent = proposal.narration;
    el('narration-block').hidden = false;
  }
}

async function runInquiry() {
  const text = el('inquiry-text').value.trim();
  const statusEl = el('status');
  if (!text) {
    statusEl.textContent = 'Write or pick a client inquiry first.';
    statusEl.className = 'status';
    return;
  }

  ['brief-block', 'questions-block', 'itinerary-block', 'narration-block'].forEach((id) => {
    el(id).hidden = true;
  });
  setStage('parsed');
  statusEl.textContent = 'Calling Azure OpenAI and running the pricing engine…';
  statusEl.className = 'status is-pending';
  el('run-btn').disabled = true;

  try {
    const res = await fetch('/api/run-inquiry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ inquiry_id: 'manual_inquiry', text }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const result = await res.json();

    renderBrief(result.brief, result.info_audit);
    renderQuestions(result.client_questions);
    renderProposal(result.proposal);

    if (result.proposal.status === 'priced') setStage('priced');
    else if (result.client_questions.questions.length) setStage('clarify');
    else setStage('priced');

    statusEl.textContent = '';
  } catch (e) {
    statusEl.textContent = `Could not generate a proposal: ${e.message}`;
    statusEl.className = 'status';
  } finally {
    el('run-btn').disabled = false;
  }
}

function initRateSheetToggle() {
  const toggle = el('rate-sheet-toggle');
  const panel = el('rate-sheet-panel');
  toggle.addEventListener('click', () => {
    const expanded = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!expanded));
    panel.hidden = expanded;
  });
}

loadRateSheet();
loadSamples();
initRateSheetToggle();
el('run-btn').addEventListener('click', runInquiry);
