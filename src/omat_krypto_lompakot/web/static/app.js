// OmatKryptoLompakot Hub Dashboard Logic

const state = {
  activeTab: 'portfolio',
  portfolio: null,
  accounts: []
};

// UI Elements
const toastEl = document.getElementById('toast');
const tabPortfolioBtn = document.getElementById('tab-portfolio-btn');
const tabAccountsBtn = document.getElementById('tab-accounts-btn');
const tabTransactionsBtn = document.getElementById('tab-transactions-btn');
const tabPortfolio = document.getElementById('tab-portfolio');
const tabAccounts = document.getElementById('tab-accounts');
const tabTransactions = document.getElementById('tab-transactions');

const addModal = document.getElementById('add-modal');
const openAddModalBtn = document.getElementById('open-add-modal-btn');
const openAddModalBtn2 = document.getElementById('open-add-modal-btn-2');
const closeModalBtn = document.getElementById('close-modal-btn');
const refreshAllBtn = document.getElementById('refresh-all-btn');

const modalTabCexBtn = document.getElementById('modal-tab-cex-btn');
const modalTabOnchainBtn = document.getElementById('modal-tab-onchain-btn');
const cexForm = document.getElementById('cex-form');
const onchainForm = document.getElementById('onchain-form');

// Toast Helper
function showToast(message, type = 'info') {
  toastEl.textContent = message;
  toastEl.className = 'fixed bottom-5 right-5 z-50 transform transition-all duration-300 max-w-sm p-4 rounded-xl shadow-2xl flex items-start space-x-3 text-xs font-semibold';
  
  if (type === 'success') {
    toastEl.classList.add('bg-emerald-900/90', 'border', 'border-emerald-500/40', 'text-emerald-200');
  } else if (type === 'error') {
    toastEl.classList.add('bg-rose-900/90', 'border', 'border-rose-500/40', 'text-rose-200');
  } else {
    toastEl.classList.add('bg-indigo-900/90', 'border', 'border-indigo-500/40', 'text-indigo-200');
  }

  toastEl.classList.remove('translate-y-20', 'opacity-0');
  setTimeout(() => {
    toastEl.classList.add('translate-y-20', 'opacity-0');
  }, 4000);
}

// Tab Switching
function switchTab(tab) {
  state.activeTab = tab;
  const activeClass = 'pb-3 text-sm font-semibold border-b-2 border-indigo-500 text-indigo-400 transition flex items-center';
  const inactiveClass = 'pb-3 text-sm font-semibold border-b-2 border-transparent text-gray-400 hover:text-gray-200 transition flex items-center';

  tabPortfolioBtn.className = tab === 'portfolio' ? activeClass : inactiveClass;
  tabAccountsBtn.className = tab === 'accounts' ? activeClass : inactiveClass;
  tabTransactionsBtn.className = tab === 'transactions' ? activeClass : inactiveClass;

  tabPortfolio.classList.toggle('hidden', tab !== 'portfolio');
  tabAccounts.classList.toggle('hidden', tab !== 'accounts');
  tabTransactions.classList.toggle('hidden', tab !== 'transactions');

  if (tab === 'transactions') {
    loadTransactions();
  }
}

tabPortfolioBtn.addEventListener('click', () => switchTab('portfolio'));
tabAccountsBtn.addEventListener('click', () => switchTab('accounts'));
tabTransactionsBtn.addEventListener('click', () => switchTab('transactions'));

// Modal Handlers
function openModal() { addModal.classList.remove('hidden'); }
function closeModal() { addModal.classList.add('hidden'); }

openAddModalBtn.addEventListener('click', openModal);
if (openAddModalBtn2) openAddModalBtn2.addEventListener('click', openModal);
closeModalBtn.addEventListener('click', closeModal);
document.getElementById('cancel-cex-btn').addEventListener('click', closeModal);
document.getElementById('cancel-onchain-btn').addEventListener('click', closeModal);

modalTabCexBtn.addEventListener('click', () => {
  modalTabCexBtn.className = 'py-2 rounded-lg bg-indigo-600 text-white transition';
  modalTabOnchainBtn.className = 'py-2 rounded-lg text-gray-400 hover:text-white transition';
  cexForm.classList.remove('hidden');
  onchainForm.classList.add('hidden');
});

modalTabOnchainBtn.addEventListener('click', () => {
  modalTabOnchainBtn.className = 'py-2 rounded-lg bg-indigo-600 text-white transition';
  modalTabCexBtn.className = 'py-2 rounded-lg text-gray-400 hover:text-white transition';
  onchainForm.classList.remove('hidden');
  cexForm.classList.add('hidden');
});

// Load Data
async function loadPortfolio() {
  try {
    const res = await fetch('/api/portfolio');
    if (!res.ok) throw new Error('Portfolion haku epäonnistui');
    const data = await res.json();
    state.portfolio = data;
    renderPortfolio(data);
  } catch (err) {
    console.error(err);
  }
}

async function loadAccounts() {
  try {
    const res = await fetch('/api/accounts');
    if (!res.ok) throw new Error('Tilien haku epäonnistui');
    const data = await res.json();
    state.accounts = data.accounts || [];
    document.getElementById('accounts-badge-count').textContent = state.accounts.length;
    renderAccounts(state.accounts);
  } catch (err) {
    console.error(err);
  }
}

function renderPortfolio(data) {
  document.getElementById('total-eur-val').textContent = (parseFloat(data.total_eur) || 0).toLocaleString('fi-FI', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';
  document.getElementById('total-usd-val').textContent = '$' + (parseFloat(data.total_usd) || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  document.getElementById('total-sources-count').textContent = data.sources ? data.sources.length : 0;
  
  if (data.as_of) {
    const dt = new Date(data.as_of);
    document.getElementById('last-updated-time').textContent = 'Päivitetty: ' + dt.toLocaleTimeString('fi-FI');
  }

  // Warnings
  const warningsBanner = document.getElementById('warnings-banner');
  const warningsList = document.getElementById('warnings-list');
  if (data.warnings && data.warnings.length > 0) {
    warningsList.innerHTML = data.warnings.map(w => `<li>${w}</li>`).join('');
    warningsBanner.classList.remove('hidden');
  } else {
    warningsBanner.classList.add('hidden');
  }

  // Table
  const tbody = document.getElementById('portfolio-groups-tbody');
  if (!data.groups || data.groups.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="px-6 py-8 text-center text-gray-500">Ei vielä liitettyjä varoja. Lisää yhteys aloittaaksesi!</td></tr>`;
    return;
  }

  tbody.innerHTML = data.groups.map(g => {
    const eurVal = g.value_eur ? (parseFloat(g.value_eur) || 0).toLocaleString('fi-FI', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €' : '-';
    const usdVal = g.value_usd ? '$' + (parseFloat(g.value_usd) || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '-';
    
    // Details pills
    const detailsPills = g.items.map(it => {
      return `<span class="inline-block px-2 py-0.5 text-[11px] rounded bg-gray-800 text-gray-300 mr-1.5 border border-gray-700/60 font-mono">${it.account_name}: ${it.amount} ${it.symbol}</span>`;
    }).join('');

    return `
      <tr class="hover:bg-gray-800/30 transition">
        <td class="px-6 py-4 font-bold text-white flex items-center space-x-2">
          <span class="w-7 h-7 rounded-lg bg-gray-800 border border-gray-700 flex items-center justify-center text-xs text-indigo-400 font-mono">${g.group_name.slice(0, 3)}</span>
          <span>${g.group_name}</span>
        </td>
        <td class="px-6 py-4 font-mono font-medium text-gray-200">${g.total_amount}</td>
        <td class="px-6 py-4 font-mono font-semibold text-emerald-400">${eurVal}</td>
        <td class="px-6 py-4 font-mono text-gray-300">${usdVal}</td>
        <td class="px-6 py-4">${detailsPills}</td>
      </tr>
    `;
  }).join('');
}

function renderAccounts(accounts) {
  const container = document.getElementById('accounts-cards-container');
  if (!accounts || accounts.length === 0) {
    container.innerHTML = `
      <div class="col-span-full glass p-10 rounded-2xl text-center text-gray-500 space-y-3">
        <p>Ei vielä liitettyjä CEX-pörssejä tai lohkoketjuosoitteita.</p>
        <button onclick="openModal()" class="px-4 py-2 text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition">
          + Lisää ensimmäinen yhteys
        </button>
      </div>
    `;
    return;
  }

  container.innerHTML = accounts.map(acc => {
    let colorBadge = '';
    if (acc.status_color === 'green') {
      colorBadge = '<span class="px-2 py-0.5 text-[10px] font-semibold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Aktiivinen</span>';
    } else if (acc.status_color === 'yellow') {
      colorBadge = '<span class="px-2 py-0.5 text-[10px] font-semibold rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">Huomio / Vanhenee</span>';
    } else {
      colorBadge = '<span class="px-2 py-0.5 text-[10px] font-semibold rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20">Virhe / Vaara</span>';
    }

    let metaDetails = '';
    if (acc.type === 'cex' && acc.credentials) {
      const c = acc.credentials;
      metaDetails = `
        <div class="text-[11px] text-gray-400 space-y-1 bg-gray-900/60 p-2.5 rounded-lg border border-gray-800">
          <div class="flex justify-between"><span>Avain:</span> <span class="font-mono text-gray-300">${c.key_hint}</span></div>
          <div class="flex justify-between"><span>Oikeudet:</span> <span class="${c.can_trade || c.can_withdraw ? 'text-rose-400 font-bold' : 'text-emerald-400'}">${c.can_trade || c.can_withdraw ? 'Kaupankäynti / Nosto (VAARA)' : 'Vain Luku'}</span></div>
          ${c.expires_at ? `<div class="flex justify-between"><span>Vanhenee:</span> <span class="font-mono">${c.expires_at.slice(0, 10)}</span></div>` : ''}
        </div>
      `;
    } else if (acc.type === 'onchain' && acc.wallets && acc.wallets.length > 0) {
      const w = acc.wallets[0];
      const shortAddr = w.address.slice(0, 6) + '...' + w.address.slice(-4);
      metaDetails = `
        <div class="text-[11px] text-gray-400 space-y-1 bg-gray-900/60 p-2.5 rounded-lg border border-gray-800">
          <div class="flex justify-between"><span>Ketju:</span> <span class="capitalize text-gray-200">${w.chain}</span></div>
          <div class="flex justify-between"><span>Osoite:</span> <span class="font-mono text-indigo-300" title="${w.address}">${shortAddr}</span></div>
        </div>
      `;
    }

    return `
      <div class="glass p-5 rounded-2xl flex flex-col justify-between space-y-4 border border-gray-800/80">
        <div class="space-y-3">
          <div class="flex items-start justify-between">
            <div>
              <div class="text-xs uppercase font-bold tracking-wider text-indigo-400">${acc.venue} (${acc.type.toUpperCase()})</div>
              <h3 class="font-bold text-base text-white mt-0.5">${acc.name}</h3>
            </div>
            ${colorBadge}
          </div>
          ${metaDetails}
          ${acc.last_error_msg ? `<p class="text-[11px] text-rose-400 bg-rose-950/30 p-2 rounded border border-rose-900/40">${acc.last_error_msg}</p>` : ''}
        </div>

        <div class="pt-2 border-t border-gray-800 flex items-center justify-between">
          <div class="space-x-1.5">
            <button onclick="testAccount('${acc.id}')" class="px-2.5 py-1 text-[11px] font-semibold bg-gray-800 hover:bg-gray-700 text-gray-200 rounded border border-gray-700 transition">Testaa</button>
            <button onclick="syncSingleAccount('${acc.id}')" class="px-2.5 py-1 text-[11px] font-semibold bg-indigo-600/20 hover:bg-indigo-600/40 text-indigo-300 border border-indigo-500/30 rounded transition">Päivitä</button>
          </div>
          <button onclick="deleteAccount('${acc.id}')" class="text-gray-500 hover:text-rose-400 text-xs p-1" title="Poista yhteys">&times; Poista</button>
        </div>
      </div>
    `;
  }).join('');
}

// Actions
async function testAccount(accountId) {
  showToast('Testataan yhteyttä...', 'info');
  try {
    const res = await fetch(`/api/accounts/${accountId}/test`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      showToast('Yhteystesti onnistui!', 'success');
      loadAccounts();
    } else {
      showToast('Virhe: ' + (data.detail || 'Testi epäonnistui'), 'error');
    }
  } catch (err) {
    showToast('Verkkovirhe yhteystestissä', 'error');
  }
}

async function syncSingleAccount(accountId) {
  showToast('Synkronoidaan tiliä...', 'info');
  try {
    const res = await fetch('/api/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: accountId, force: true })
    });
    const data = await res.json();
    showToast('Tili synkronoitu!', 'success');
    loadPortfolio();
    loadAccounts();
  } catch (err) {
    showToast('Synkronointi epäonnistui', 'error');
  }
}

async function deleteAccount(accountId) {
  if (!confirm('Haluatko varmasti poistaa tämän yhteyden ja sen saldot?')) return;
  try {
    const res = await fetch(`/api/accounts/${accountId}`, { method: 'DELETE' });
    if (res.ok) {
      showToast('Yhteys poistettu', 'info');
      loadPortfolio();
      loadAccounts();
    }
  } catch (err) {
    showToast('Poisto epäonnistui', 'error');
  }
}

refreshAllBtn.addEventListener('click', async () => {
  showToast('Käynnistetään koko portfolion päivitys...', 'info');
  try {
    const res = await fetch('/api/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: true })
    });
    showToast('Kaikki tilit synkronoitu!', 'success');
    loadPortfolio();
    loadAccounts();
  } catch (err) {
    showToast('Päivitys epäonnistui', 'error');
  }
});

// Form Submissions
cexForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = document.getElementById('cex-name').value.trim();
  const apiKey = document.getElementById('cex-api-key').value.trim();
  const apiSecret = document.getElementById('cex-api-secret').value.trim();
  const expires = document.getElementById('cex-expires').value;

  showToast('Testataan ja tallennetaan Kraken-avainta...', 'info');
  try {
    const res = await fetch('/api/accounts/cex', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        venue: 'kraken',
        api_key: apiKey,
        api_secret: apiSecret,
        expires_at: expires ? expires + 'T00:00:00Z' : null
      })
    });
    const data = await res.json();
    if (res.ok) {
      showToast('Kraken-yhteys luotu ja saldot haettu!', 'success');
      closeModal();
      cexForm.reset();
      loadPortfolio();
      loadAccounts();
    } else {
      showToast('Virhe: ' + (data.detail || 'Tallennus epäonnistui'), 'error');
    }
  } catch (err) {
    showToast('Verkkovirhe Kraken-yhteyden tallennuksessa', 'error');
  }
});

onchainForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const chain = document.getElementById('onchain-chain').value;
  const name = document.getElementById('onchain-name').value.trim();
  const address = document.getElementById('onchain-address').value.trim();

  showToast(`Tarkistetaan ja tallennetaan ${chain}-osoitetta...`, 'info');
  try {
    const res = await fetch('/api/accounts/onchain', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        chain,
        address,
        label: name
      })
    });
    const data = await res.json();
    if (res.ok) {
      showToast(`${chain.toUpperCase()}-lompakko lisätty ja saldot haettu!`, 'success');
      closeModal();
      onchainForm.reset();
      loadPortfolio();
      loadAccounts();
    } else {
      showToast('Virhe: ' + (data.detail || 'Tallennus epäonnistui'), 'error');
    }
  } catch (err) {
    showToast('Verkkovirhe osoitteen tallennuksessa', 'error');
  }
});

// Transactions Logic
async function loadTransactions() {
  const accountId = document.getElementById('tx-filter-account').value;
  const kind = document.getElementById('tx-filter-kind').value;
  const hideSpam = document.getElementById('tx-filter-hide-spam').checked;

  const params = new URLSearchParams();
  if (accountId) params.append('account_id', accountId);
  if (kind) params.append('kind', kind);
  if (!hideSpam) params.append('include_spam', 'true');
  params.append('limit', '100');

  try {
    const res = await fetch(`/api/transactions?${params.toString()}`);
    if (!res.ok) throw new Error('Transaktioiden haku epäonnistui');
    const data = await res.json();
    renderTransactions(data.transactions || [], data.total || 0);
  } catch (err) {
    console.error(err);
  }
}

function renderTransactions(txs, total) {
  document.getElementById('tx-count-badge').textContent = `${total} tapahtumaa`;
  const tbody = document.getElementById('transactions-tbody');

  if (!txs || txs.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" class="px-6 py-8 text-center text-gray-500">Ei löytyneitä transaktioita annetuilla suodattimilla.</td></tr>`;
    return;
  }

  tbody.innerHTML = txs.map(tx => {
    let kindBadge = '';
    if (tx.kind === 'trade') {
      kindBadge = '<span class="px-2 py-0.5 text-[10px] font-semibold rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">Kauppa</span>';
    } else if (tx.kind === 'deposit') {
      kindBadge = '<span class="px-2 py-0.5 text-[10px] font-semibold rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Talletus</span>';
    } else if (tx.kind === 'withdrawal') {
      kindBadge = '<span class="px-2 py-0.5 text-[10px] font-semibold rounded bg-amber-500/10 text-amber-400 border border-amber-500/20">Nosto</span>';
    } else if (tx.kind === 'approval') {
      kindBadge = '<span class="px-2 py-0.5 text-[10px] font-semibold rounded bg-purple-500/10 text-purple-400 border border-purple-500/20">Lupakutsu (Approval)</span>';
    } else {
      kindBadge = '<span class="px-2 py-0.5 text-[10px] font-semibold rounded bg-gray-700/50 text-gray-300 border border-gray-600/30">Siirto</span>';
    }

    const dt = new Date(tx.timestamp);
    const timeStr = dt.toLocaleString('fi-FI');
    const shortHash = tx.hash.length > 16 ? `${tx.hash.slice(0, 10)}...${tx.hash.slice(-6)}` : tx.hash;
    const tokenSymbol = tx.asset_symbol || (tx.chain === 'ethereum' ? 'ETH' : '');
    const amountStr = tx.amount ? `${tx.amount} ${tokenSymbol}` : '-';

    let statusBadge = '<span class="text-xs text-gray-400">Vahvistettu</span>';
    if (tx.is_spam) {
      statusBadge = `<span class="px-2 py-0.5 text-[10px] font-semibold rounded bg-rose-500/20 text-rose-300 border border-rose-500/30" title="${tx.spam_reason || 'Roskatransaktio'}">Roska</span>`;
    }

    return `
      <tr class="hover:bg-gray-800/30 transition text-xs">
        <td class="px-6 py-3.5 font-mono text-gray-300">${timeStr}</td>
        <td class="px-6 py-3.5 font-medium text-white">${tx.account_name} <span class="text-[10px] text-gray-500 uppercase font-mono">(${tx.venue})</span></td>
        <td class="px-6 py-3.5">${kindBadge}</td>
        <td class="px-6 py-3.5 font-mono font-medium text-gray-200">${amountStr}</td>
        <td class="px-6 py-3.5 font-mono text-indigo-400" title="${tx.hash}">${shortHash}</td>
        <td class="px-6 py-3.5">${statusBadge}</td>
      </tr>
    `;
  }).join('');
}

document.getElementById('apply-tx-filters-btn').addEventListener('click', loadTransactions);
document.getElementById('tx-filter-hide-spam').addEventListener('change', loadTransactions);

// Update account select in transactions tab
function updateAccountSelect(accounts) {
  const sel = document.getElementById('tx-filter-account');
  const currentVal = sel.value;
  sel.innerHTML = '<option value="">Kaikki tilit</option>' + accounts.map(a => `<option value="${a.id}">${a.name} (${a.venue})</option>`).join('');
  sel.value = currentVal;
}

// Initial Load
document.addEventListener('DOMContentLoaded', () => {
  loadPortfolio();
  loadAccounts().then(() => {
    updateAccountSelect(state.accounts);
  });
});
