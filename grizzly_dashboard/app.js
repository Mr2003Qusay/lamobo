let apiMode = 'partner';
let config = {
    apiKey: '6323444b7a20389c793f34496ca0b385',
    bearerToken: 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJodHRwczovL2dyaXp6bHlzbXMuY29tIiwiYXVkIjoiaHR0cHM6Ly9ncml6emx5c21zLmNvbSIsImp0aSI6IjE2MjEzNzgiLCJ1aWQiOjE2MjEzNzgsImlhdCI6MTc3OTgzMTMwOS4zMzE2MTYsImV4cCI6MTExMTEwMzEzMDkuMzMxNjE1fQ.8W-Wk1IK1P9qis_80cizfIx1nyV9-3xlGe4DCeOltVE',
    sessionToken: 'frntsu7329i83',
    pvacodesKey: '6a18ec0b5a6b1202605290659476a18ec0b5a6b7',
    pvacodesCookie: '',
    otpdoctorKey: 'p5lqciijck788wu3tkku9mpofswlyr0v'
};
let modeDefaultServices = {
    partner: 'Jio5, Jio10, Jio11',
    user: 'Jio5, Jio10, Jio11',
    pvacodes: 'Jio5',
    otpdoctor: 'My jio.com - 🇮🇳5'
};
let modeDefaultPrices = {
    partner: '0.1350',
    user: '0.1350',
    pvacodes: '0.1350',
    otpdoctor: '20.0'
};
const pvacodesCountryMap = {
    "22": "India",
    "0": "Russia",
    "12": "USA",
    "1": "Ukraine",
    "7": "Kazakhstan",
    "9": "China",
    "15": "United Kingdom",
    "8": "Kyrgyzstan",
    "16": "Indonesia",
    "11": "Brazil"
};
const otpdoctorCountryMap = {
    "22": "in",
    "0": "ru",
    "12": "us",
    "1": "ua",
    "7": "kz",
    "9": "cn",
    "15": "uk",
    "8": "kg",
    "16": "id",
    "11": "br"
};
let activeRentals = {};

// Sliding window rate-limiter for PVACodes to prevent exceeding the global 90 requests/minute limit.
// Allows concurrent burst requests to execute instantly, but enforces a maximum of 80 requests per rolling 60 seconds.
const pvacodesRequestTimestamps = [];
const MAX_PVACODES_REQUESTS_PER_MINUTE = 80;

async function fetchPvacodes(url, options = {}) {
    while (true) {
        const now = Date.now();
        // Clear timestamps older than 60 seconds
        while (pvacodesRequestTimestamps.length > 0 && pvacodesRequestTimestamps[0] < now - 60000) {
            pvacodesRequestTimestamps.shift();
        }
        
        if (pvacodesRequestTimestamps.length < MAX_PVACODES_REQUESTS_PER_MINUTE) {
            break;
        }
        
        // Wait for the oldest request to fall out of the 60-second window
        const oldestRequestTime = pvacodesRequestTimestamps[0];
        const waitTime = oldestRequestTime + 60000 - now + 100; // add 100ms buffer
        await new Promise(resolve => setTimeout(resolve, Math.max(50, waitTime)));
    }
    
    pvacodesRequestTimestamps.push(Date.now());
    return fetch(url, options);
}

let pollInterval = null;
let pollCountdown = 10;
let pollCountdownInterval = null;
let isAutobuyRunning = false;
let stopAutoBuy = false;
let activeAutoBuyLoops = 0;
let validNumbers = [];
let scheduledCancellations = [];
let smsNotifiedIds = new Set();
const lastPvaRefreshTime = {};
let isServerOffline = false;

function setServerOfflineState(offline) {
    const indicator = document.querySelector('.status-indicator');
    const statusText = document.querySelector('.status-text');
    if (!indicator || !statusText) return;
    
    if (offline) {
        if (!isServerOffline) {
            isServerOffline = true;
            indicator.classList.remove('online');
            indicator.classList.add('offline');
            statusText.textContent = 'Proxy Server Offline';
            
            const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
            const tip = isLocal 
                ? 'Try opening http://127.0.0.1:8000 directly to avoid IPv6 localhost issues.'
                : 'Please open http://127.0.0.1:8000 on the host PC and click "Allow other devices".';
            
            showToast('error', 'Connection Error', `Could not reach local proxy server. ${tip}`);
        }
    } else {
        if (isServerOffline) {
            isServerOffline = false;
            indicator.classList.remove('offline');
            indicator.classList.add('online');
            statusText.textContent = 'Proxy Server Connected';
            showToast('success', 'Connected', 'Proxy server is back online!');
        }
    }
}

async function safeParseJSON(resp) {
    const text = await resp.text();
    try {
        return JSON.parse(text);
    } catch (e) {
        // Try to extract the first valid JSON object from text with trailing garbage
        const match = text.match(/^\s*([{\[])/);
        if (!match) throw new Error('Response is not JSON: ' + text.substring(0, 200));
        const openChar = match[1];
        const closeChar = openChar === '{' ? '}' : ']';
        let depth = 0, inStr = false, esc = false;
        for (let i = text.indexOf(openChar); i < text.length; i++) {
            const c = text[i];
            if (esc) { esc = false; continue; }
            if (c === '\\' && inStr) { esc = true; continue; }
            if (c === '"') { inStr = !inStr; continue; }
            if (inStr) continue;
            if (c === openChar) depth++;
            else if (c === closeChar) {
                depth--;
                if (depth === 0) {
                    return JSON.parse(text.substring(text.indexOf(openChar), i + 1));
                }
            }
        }
        throw new Error('Could not extract valid JSON from response: ' + text.substring(0, 200));
    }
}

const toggleConfigBtn = document.getElementById('toggle-config-btn');
const configCard = document.querySelector('.config-card');
const modePartnerBtn = document.getElementById('mode-partner-btn');
const modeUserBtn = document.getElementById('mode-user-btn');
const modePvacodesBtn = document.getElementById('mode-pvacodes-btn');
const modeOtpdoctorBtn = document.getElementById('mode-otpdoctor-btn');
const partnerInputs = document.getElementById('partner-inputs');
const userInputs = document.getElementById('user-inputs');
const pvacodesInputs = document.getElementById('pvacodes-inputs');
const otpdoctorInputs = document.getElementById('otpdoctor-inputs');
const saveConfigBtn = document.getElementById('save-config-btn');
const refreshBalanceBtn = document.getElementById('refresh-balance-btn');
const balanceValEl = document.getElementById('balance-val');
const balanceSubtextEl = document.getElementById('balance-subtext');
const rentBtn = document.getElementById('rent-btn');
const autobuyBtn = document.getElementById('autobuy-btn');
const countrySelect = document.getElementById('country-select');

function getServicesForMode(mode) {
    const el = document.getElementById(`autobuy-${mode}-services`);
    return el ? el.value.trim() : '';
}

function getMaxPriceForMode(mode) {
    const el = document.getElementById(`autobuy-${mode}-price`);
    return el ? el.value.trim() : '0.1350';
}

const activeGrid = document.getElementById('active-grid');
const noRentalsEl = document.getElementById('no-rentals');
const pollTimerEl = document.getElementById('poll-timer');
const beepSound = document.getElementById('beep-sound');
const testNumberBtn = document.getElementById('test-number-btn');
const testAllBtn = document.getElementById('test-all-btn');
const testNumberInput = document.getElementById('test-number-input');
const testResultBox = document.getElementById('test-result');
const resultStatus = document.getElementById('result-status');
const resultDetails = document.getElementById('result-details');

document.addEventListener('DOMContentLoaded', () => {
    loadSavedConfig();
    setupEventListeners();
    refreshAll();
    startPolling();
    checkNetworkStatus();
});

function setupEventListeners() {
    toggleConfigBtn.addEventListener('click', () => {
        configCard.classList.toggle('collapsed');
    });
    modePartnerBtn.addEventListener('click', () => {
        setApiMode('partner');
    });
    modeUserBtn.addEventListener('click', () => {
        setApiMode('user');
    });
    modePvacodesBtn.addEventListener('click', () => {
        setApiMode('pvacodes');
    });
    modeOtpdoctorBtn.addEventListener('click', () => {
        setApiMode('otpdoctor');
    });
    saveConfigBtn.addEventListener('click', () => {
        saveConfig();
        showToast('info', 'Configuration Updated', 'Credentials applied to current session.');
        refreshAll();
    });
    refreshBalanceBtn.addEventListener('click', () => {
        refreshBalance();
    });
    rentBtn.addEventListener('click', () => {
        orderNumber();
    });
    autobuyBtn.addEventListener('click', () => {
        if (isAutobuyRunning) {
            stopAutoBuy = true;
            autobuyBtn.innerHTML = '<i class="fa-solid fa-stop"></i> Stopping...';
            autobuyBtn.disabled = true;
            showToast('info', 'Stopping AutoBuy', 'Will stop buying new numbers. Existing cancellations will continue.');
        } else {
            startAutoBuy();
        }
    });
    testNumberBtn.addEventListener('click', testSingleNumber);
    testAllBtn.addEventListener('click', testAllActiveNumbers);
    
    // Add input event listeners to save settings for all modes
    ['partner', 'user', 'pvacodes', 'otpdoctor'].forEach(mode => {
        const sEl = document.getElementById(`autobuy-${mode}-services`);
        if (sEl) sEl.addEventListener('input', saveConfig);
        const pEl = document.getElementById(`autobuy-${mode}-price`);
        if (pEl) pEl.addEventListener('input', saveConfig);
    });
    document.getElementById('clear-valid-btn').addEventListener('click', clearValidNumbers);
    document.getElementById('export-valid-btn').addEventListener('click', exportValidNumbers);
    document.getElementById('copy-all-valid-btn').addEventListener('click', copyAllValidNumbers);
    document.getElementById('toggle-network-btn').addEventListener('click', toggleNetworkSharing);
    document.getElementById('copy-network-ip-btn').addEventListener('click', copyNetworkIp);

    const pollingToggleCheck = document.getElementById('polling-toggle-check');
    if (pollingToggleCheck) {
        pollingToggleCheck.addEventListener('change', () => {
            if (pollingToggleCheck.checked) {
                startPolling();
                showToast('success', 'Auto-Polling Enabled', 'SMS and active numbers background checks are running.');
            } else {
                stopPolling();
                showToast('info', 'Auto-Polling Disabled', 'SMS background checks are paused.');
            }
        });
    }
}

function loadSavedConfig() {
    const saved = localStorage.getItem('grizzlysms_dashboard_config');
    if (saved) {
        try {
            const parsed = JSON.parse(saved);
            config = { ...config, ...parsed.config };
            apiMode = parsed.apiMode || 'partner';
            if (parsed.modeDefaultServices) {
                modeDefaultServices = { ...modeDefaultServices, ...parsed.modeDefaultServices };
            }
            if (parsed.modeDefaultPrices) {
                modeDefaultPrices = { ...modeDefaultPrices, ...parsed.modeDefaultPrices };
            }
            if (!config.otpdoctorKey) {
                config.otpdoctorKey = 'p5lqciijck788wu3tkku9mpofswlyr0v';
            }
            document.getElementById('api-key-input').value = config.apiKey || '';
            document.getElementById('bearer-token-input').value = config.bearerToken || '';
            document.getElementById('session-token-input').value = config.sessionToken || '';
            document.getElementById('pvacodes-key-input').value = config.pvacodesKey || '';
            document.getElementById('pvacodes-cookie-input').value = config.pvacodesCookie || '';
            document.getElementById('otpdoctor-key-input').value = config.otpdoctorKey || '';
            
            // Pre-populate input values from modeDefaultServices and modeDefaultPrices
            ['partner', 'user', 'pvacodes', 'otpdoctor'].forEach(mode => {
                const sEl = document.getElementById(`autobuy-${mode}-services`);
                if (sEl) sEl.value = modeDefaultServices[mode] || '';
                const pEl = document.getElementById(`autobuy-${mode}-price`);
                if (pEl) pEl.value = modeDefaultPrices[mode] || '';
            });

            setApiMode(apiMode);
        } catch (e) {
            console.error('Failed to parse saved config', e);
        }
    }
}

function saveConfig() {
    config.apiKey = document.getElementById('api-key-input').value.trim();
    config.bearerToken = document.getElementById('bearer-token-input').value.trim();
    config.sessionToken = document.getElementById('session-token-input').value.trim();
    config.pvacodesKey = document.getElementById('pvacodes-key-input').value.trim();
    config.pvacodesCookie = document.getElementById('pvacodes-cookie-input').value.trim();
    config.otpdoctorKey = document.getElementById('otpdoctor-key-input').value.trim();
    
    // Save from input values
    ['partner', 'user', 'pvacodes', 'otpdoctor'].forEach(mode => {
        const sEl = document.getElementById(`autobuy-${mode}-services`);
        if (sEl) modeDefaultServices[mode] = sEl.value.trim();
        const pEl = document.getElementById(`autobuy-${mode}-price`);
        if (pEl) modeDefaultPrices[mode] = pEl.value.trim();
    });

    localStorage.setItem('grizzlysms_dashboard_config', JSON.stringify({
        config,
        apiMode,
        modeDefaultServices,
        modeDefaultPrices
    }));
}

function setApiMode(mode) {
    apiMode = mode;
    [modePartnerBtn, modeUserBtn, modePvacodesBtn, modeOtpdoctorBtn].forEach(btn => btn.classList.remove('active'));
    partnerInputs.classList.add('hidden');
    userInputs.classList.add('hidden');
    pvacodesInputs.classList.add('hidden');
    otpdoctorInputs.classList.add('hidden');

    if (mode === 'partner') {
        modePartnerBtn.classList.add('active');
        partnerInputs.classList.remove('hidden');
        balanceSubtextEl.innerHTML = '<i class="fa-solid fa-key"></i> Partner API Key Mode';
    } else if (mode === 'user') {
        modeUserBtn.classList.add('active');
        userInputs.classList.remove('hidden');
        balanceSubtextEl.innerHTML = '<i class="fa-solid fa-shield"></i> User JWT Token Mode';
    } else if (mode === 'pvacodes') {
        modePvacodesBtn.classList.add('active');
        pvacodesInputs.classList.remove('hidden');
        balanceSubtextEl.innerHTML = '<i class="fa-solid fa-key"></i> PVACodes API Key Mode';
    } else if (mode === 'otpdoctor') {
        modeOtpdoctorBtn.classList.add('active');
        otpdoctorInputs.classList.remove('hidden');
        balanceSubtextEl.innerHTML = '<i class="fa-solid fa-key"></i> OTP Doctor API Key Mode';
    }

    configCard.classList.add('collapsed');
    startPolling();
}

async function refreshBalanceForMode(targetMode) {
    try {
        let url = '';
        if (targetMode === 'partner') {
            if (!config.apiKey) return 0.00;
            url = `/api/balance?api_key=${encodeURIComponent(config.apiKey)}`;
        } else if (targetMode === 'user') {
            if (!config.bearerToken || !config.sessionToken) return 0.00;
            url = `/api/user-balance?token=${encodeURIComponent(config.bearerToken)}&session=${encodeURIComponent(config.sessionToken)}`;
        } else if (targetMode === 'pvacodes') {
            if (!config.pvacodesKey) return 0.00;
            url = `/api/pvacodes/balance?key=${encodeURIComponent(config.pvacodesKey)}`;
        } else if (targetMode === 'otpdoctor') {
            if (!config.otpdoctorKey) return 0.00;
            url = `/api/otpdoctor/balance?api_key=${encodeURIComponent(config.otpdoctorKey)}`;
        } else {
            return 0.00;
        }

        const resp = await (targetMode === 'pvacodes' ? fetchPvacodes(url) : fetch(url));
        if (!resp.ok) throw new Error('API server returned error');
        setServerOfflineState(false);
        let balance = '0.00';
        
        if (targetMode === 'partner' || targetMode === 'otpdoctor') {
            const txt = await resp.text();
            if (txt.includes('ACCESS_BALANCE:')) {
                balance = txt.split(':')[1];
            } else if (txt.includes('BAD_KEY')) {
                if (targetMode === apiMode) {
                    showToast('error', 'Unauthorized', 'Invalid API Key provided.');
                }
                balance = 'ERROR';
            } else {
                balance = txt;
            }
        } else if (targetMode === 'user') {
            const data = await resp.json();
            if (data.error) {
                if (targetMode === apiMode) {
                    showToast('error', 'Error fetching balance', data.error);
                }
                balance = 'ERROR';
            } else if (data.value !== undefined) {
                balance = data.value;
            } else {
                balance = parseFloat(data).toFixed(2);
            }
        } else if (targetMode === 'pvacodes') {
            const data = await resp.json();
            if (data.status && data.status.code === '1000') {
                if (data.data && data.data.credits !== undefined) {
                    balance = parseFloat(data.data.credits).toFixed(2);
                } else {
                    balance = '0.00';
                }
            } else if (data.status && data.status.code === '1003') {
                if (targetMode === apiMode) {
                    showToast('error', 'Insufficient Balance', data.status.message);
                }
                balance = 'ERROR';
            } else {
                if (targetMode === apiMode) {
                    showToast('error', 'Error', data.status?.message || 'Unknown error');
                }
                balance = 'ERROR';
            }
        }

        // Update UI only if targetMode matches currently active apiMode
        if (targetMode === apiMode) {
            balanceValEl.textContent = balance;
            if (balance !== 'ERROR') {
                const symbol = targetMode === 'otpdoctor' ? '₹' : '$';
                balanceSubtextEl.innerHTML = `<i class="fa-solid fa-circle-check"></i> Refreshed balance: ${symbol}${balance}`;
            } else {
                balanceSubtextEl.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Error loading balance`;
            }
        }

        if (balance === 'ERROR') return 0.00;
        const parsed = parseFloat(balance);
        return isNaN(parsed) ? 0.00 : parsed;

    } catch (e) {
        setServerOfflineState(true);
        if (targetMode === apiMode) {
            balanceValEl.textContent = 'ERROR';
            balanceSubtextEl.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Connection failed`;
        }
        return 0.00;
    }
}

async function refreshBalance() {
    refreshBalanceBtn.classList.add('fa-spin');
    try {
        await refreshBalanceForMode(apiMode);
    } finally {
        setTimeout(() => refreshBalanceBtn.classList.remove('fa-spin'), 600);
    }
}

async function refreshActiveRentals() {
    try {
        let newRentals = {};
        
        // 1. Fetch Grizzly Partner if key is loaded
        if (config.apiKey) {
            try {
                const resp = await fetch(`/api/active-rentals?api_key=${encodeURIComponent(config.apiKey)}`);
                if (resp.ok) {
                    const data = await resp.json();
                    if (Array.isArray(data)) {
                        data.forEach(item => {
                            const id = String(item.activationId);
                            newRentals[id] = {
                                id: id,
                                phoneNumber: item.phoneNumber,
                                cost: item.activationCost || 0.1350,
                                service: item.serviceCode || 'unknown',
                                country: item.countryName || item.countryCode || 'N/A',
                                status: item.activationStatus,
                                code: item.smsCode || '',
                                smsText: item.smsText || '',
                                endTime: new Date(item.activationTime).getTime() + (15 * 60 * 1000),
                                provider: 'Grizzly Partner',
                                providerClass: 'partner'
                            };
                        });
                    }
                }
            } catch (err) { console.error('Error fetching Grizzly Partner active rentals:', err); }
        }
        
        // 2. Fetch Grizzly User if token and session are loaded
        if (config.bearerToken && config.sessionToken) {
            try {
                const resp = await fetch(`/api/user-numbers?token=${encodeURIComponent(config.bearerToken)}&session=${encodeURIComponent(config.sessionToken)}`);
                if (resp.ok) {
                    const data = await resp.json();
                    if (Array.isArray(data)) {
                        data.forEach(item => {
                            const id = String(item.id);
                            const endTime = item.timestamp_end ? item.timestamp_end * 1000 : new Date(item.end_at).getTime();
                            newRentals[id] = {
                                id: id,
                                phoneNumber: item.number,
                                cost: item.price || 0.1350,
                                service: (item.service && item.service.external_id) || 'unknown',
                                country: item.countryCode || 'India (22)',
                                status: item.status,
                                code: item.code || '',
                                smsText: item.code ? `SMS Code: ${item.code}` : '',
                                endTime: endTime,
                                provider: 'Grizzly User',
                                providerClass: 'user'
                            };
                        });
                    }
                }
            } catch (err) { console.error('Error fetching Grizzly User active rentals:', err); }
        }

        // 3. Fetch PVACodes if key is loaded
        if (config.pvacodesKey) {
            try {
                const resp = await fetchPvacodes(`/api/pvacodes/history?key=${encodeURIComponent(config.pvacodesKey)}`);
                if (resp.ok) {
                    const data = await resp.json();
                    if (data.status && data.status.code === '1000' && Array.isArray(data.data)) {
                        data.data.forEach(item => {
                            const id = String(item.number_id);
                            newRentals[id] = {
                                id: id,
                                phoneNumber: item.number.replace(/^\+/, ''),
                                cost: 0,
                                service: item.app,
                                country: item.country,
                                status: item.status,
                                code: item.sms || item.code || '',
                                smsText: item.sms || item.code || '',
                                endTime: Date.now() + (15 * 60 * 1000),
                                provider: 'PVACodes',
                                providerClass: 'pvacodes'
                            };
                        });
                    }
                }
            } catch (err) { console.error('Error fetching PVACodes active rentals:', err); }
        }

        // 4. Merge OTP Doctor, locally tracked numbers, and recently removed cards to prevent layout shift
        Object.keys(activeRentals).forEach(id => {
            const r = activeRentals[id];
            if (r.removedAt && (Date.now() - r.removedAt < 10000)) {
                // Preserve recently cancelled/completed cards locally so they fade out nicely
                newRentals[id] = r;
            } else if (r.providerClass === 'otpdoctor' || (r.providerClass === 'pvacodes' && !newRentals[id])) {
                // Keep active/pending OTP Doctor and PVACodes items if they are not in newRentals
                if (r.status !== 'cancelled' && r.status !== 'completed') {
                    newRentals[id] = r;
                }
            }
        });

        // Trigger SMS notifications for any newly received codes
        Object.keys(newRentals).forEach(id => {
            const oldItem = activeRentals[id];
            const newItem = newRentals[id];
            if (newItem.code && (!oldItem || !oldItem.code) && !smsNotifiedIds.has(id)) {
                smsNotifiedIds.add(id);
                playBeep();
                showToast('success', 'SMS Code Received!', `${newItem.phoneNumber} (${newItem.provider}): ${newItem.code}`);
                sendTelegramMessage(`💬 *SMS Received (${newItem.provider})!*\nPhone: \`+${newItem.phoneNumber}\`\nOTP Code: \`${newItem.code}\`\nActivation ID: \`${id}\``);
            }
        });

        activeRentals = newRentals;
        renderActiveGrid();
        setServerOfflineState(false);
    } catch (e) {
        console.error('Error refreshing active numbers', e);
        setServerOfflineState(true);
    }
}

function renderActiveGrid() {
    const ids = Object.keys(activeRentals);
    if (ids.length === 0) {
        noRentalsEl.classList.remove('hidden');
        activeGrid.classList.add('hidden');
        activeGrid.innerHTML = '';
        return;
    }
    noRentalsEl.classList.add('hidden');
    activeGrid.classList.remove('hidden');
    
    // 1. Remove cards that are no longer active
    const existingCards = activeGrid.querySelectorAll('.active-card');
    existingCards.forEach(card => {
        const id = card.id.replace('card-', '');
        if (!activeRentals[id]) {
            card.remove();
        }
    });
    
    // 2. Add or update active cards
    ids.forEach(id => {
        const item = activeRentals[id];
        const existingCard = document.getElementById(`card-${id}`);
        
        if (existingCard) {
            // Update classes
            const isCancelled = item.status === 'cancelled';
            const isCompleted = item.status === 'completed';
            const isRemoved = isCancelled || isCompleted;
            
            existingCard.className = `glass-card active-card ${item.providerClass || 'partner'} ${isRemoved ? 'removed-card' : ''} ${isCancelled ? 'cancelled-card' : ''} ${isCompleted ? 'completed-card' : ''}`;
            
            // Update action area
            const actionsArea = existingCard.querySelector('.card-actions');
            if (actionsArea) {
                if (isCancelled && !actionsArea.classList.contains('status-only')) {
                    actionsArea.innerHTML = `<div class="card-status-pill cancelled"><i class="fa-solid fa-circle-xmark"></i> Cancelled</div>`;
                    actionsArea.className = 'card-actions status-only';
                } else if (isCompleted && !actionsArea.classList.contains('status-only')) {
                    actionsArea.innerHTML = `<div class="card-status-pill completed"><i class="fa-solid fa-circle-check"></i> Completed</div>`;
                    actionsArea.className = 'card-actions status-only';
                }
            }
            
            // Hide progress countdown if removed
            if (isRemoved) {
                const progressContainer = existingCard.querySelector('.countdown-bar-container');
                if (progressContainer) progressContainer.style.display = 'none';
            }
            
            // Update SMS display box if the code state changed
            const smsBox = existingCard.querySelector('.sms-display-box');
            const isCodeReceived = !!item.code;
            
            if (smsBox) {
                const wasCodeReceived = smsBox.classList.contains('received');
                if (isCodeReceived !== wasCodeReceived) {
                    if (isCodeReceived) {
                        smsBox.className = 'sms-display-box received';
                        smsBox.innerHTML = `
                            <div class="sms-code-wrapper" ${isRemoved ? '' : `onclick="copyText('${item.code}', 'OTP Code copied to clipboard.')"`}>
                                <span class="sms-code-label">INCOMING OTP CODE</span>
                                <span class="sms-code-value">${item.code}</span>
                                ${item.smsText ? `<div class="sms-full-text">${item.smsText}</div>` : ''}
                            </div>
                        `;
                    } else {
                        smsBox.className = 'sms-display-box';
                        smsBox.innerHTML = `
                            <div class="sms-spinner"><i class="fa-solid fa-circle-notch fa-spin"></i></div>
                            <div class="sms-status-text">Waiting for incoming SMS...</div>
                        `;
                    }
                } else if (isCodeReceived) {
                    // Update full text if it changed
                    const fullTextEl = smsBox.querySelector('.sms-full-text');
                    if (fullTextEl && item.smsText && fullTextEl.textContent !== item.smsText) {
                        fullTextEl.textContent = item.smsText;
                    }
                }
            }
        } else {
            // Create and append new card
            const card = createActiveCard(item);
            activeGrid.appendChild(card);
        }
    });
}

function markRentalRemovedLocally(id, finalStatus) {
    if (activeRentals[id]) {
        activeRentals[id].status = finalStatus;
        activeRentals[id].removedAt = Date.now();
        renderActiveGrid();
        
        setTimeout(() => {
            if (activeRentals[id] && activeRentals[id].removedAt && (Date.now() - activeRentals[id].removedAt >= 9500)) {
                delete activeRentals[id];
                renderActiveGrid();
            }
        }, 10000);
    }
}

function createActiveCard(item) {
    const card = document.createElement('div');
    const isCancelled = item.status === 'cancelled';
    const isCompleted = item.status === 'completed';
    const isRemoved = isCancelled || isCompleted;
    
    card.className = `glass-card active-card ${item.providerClass || 'partner'} ${isRemoved ? 'removed-card' : ''} ${isCancelled ? 'cancelled-card' : ''} ${isCompleted ? 'completed-card' : ''}`;
    card.id = `card-${item.id}`;
    const isCodeReceived = !!item.code;
    const smsBoxClass = isCodeReceived ? 'sms-display-box received' : 'sms-display-box';
    const formattedCountry = item.country.toString().includes('(') ? item.country : `Country: ${item.country}`;
    
    // Icon mapping for badge
    let providerIcon = 'fa-paw';
    if (item.providerClass === 'user') providerIcon = 'fa-user-shield';
    if (item.providerClass === 'pvacodes') providerIcon = 'fa-shield-halved';
    if (item.providerClass === 'otpdoctor') providerIcon = 'fa-user-doctor';
    
    const providerBadge = `<span class="card-provider-badge ${item.providerClass || 'partner'}"><i class="fa-solid ${providerIcon}"></i> ${item.provider || 'Grizzly Partner'}</span>`;

    // Render actions based on status
    let actionsHtml = '';
    if (isCancelled) {
        actionsHtml = `<div class="card-status-pill cancelled"><i class="fa-solid fa-circle-xmark"></i> Cancelled</div>`;
    } else if (isCompleted) {
        actionsHtml = `<div class="card-status-pill completed"><i class="fa-solid fa-circle-check"></i> Completed</div>`;
    } else {
        actionsHtml = `
            <button class="card-btn card-btn-cancel" onclick="cancelRental('${item.id}')">
                <i class="fa-solid fa-circle-xmark"></i> Cancel
            </button>
            <button class="card-btn card-btn-complete" onclick="completeRental('${item.id}')">
                <i class="fa-solid fa-circle-check"></i> Complete
            </button>
        `;
    }

    card.innerHTML = `
        <div class="card-row-top">
            <span class="card-service-tag"><i class="fa-solid fa-mobile-screen"></i> ${item.service}</span>
            <span class="card-country-badge">${formattedCountry}</span>
            ${providerBadge}
        </div>
        <div class="card-number-wrapper" ${isRemoved ? '' : `onclick="copyText('${item.phoneNumber}', 'Phone number copied to clipboard.')"`}>
            <label>PHONE NUMBER ${isRemoved ? '' : '<span class="copy-hint"><i class="fa-regular fa-copy"></i> Click to copy</span>'}</label>
            <div class="card-phone-number"><i class="fa-solid fa-phone"></i> +${item.phoneNumber}</div>
        </div>
        <div class="card-meta-details">
            <div class="meta-item">
                <span class="label">Activation ID</span>
                <span class="val">${item.id}</span>
            </div>
            <div class="meta-item">
                <span class="label">Rental Price</span>
                <span class="val">${item.providerClass === 'otpdoctor' ? '₹' : '$'}${parseFloat(item.cost).toFixed(4)}</span>
            </div>
        </div>
        <div class="${smsBoxClass}">
            ${isCodeReceived ? `
                <div class="sms-code-wrapper" ${isRemoved ? '' : `onclick="copyText('${item.code}', 'OTP Code copied to clipboard.')"`}>
                    <span class="sms-code-label">INCOMING OTP CODE</span>
                    <span class="sms-code-value">${item.code}</span>
                    ${item.smsText ? `<div class="sms-full-text">${item.smsText}</div>` : ''}
                </div>
            ` : `
                <div class="sms-spinner"><i class="fa-solid fa-circle-notch fa-spin"></i></div>
                <div class="sms-status-text">Waiting for incoming SMS...</div>
            `}
        </div>
        ${isRemoved ? '' : `
        <div class="countdown-bar-container">
            <div class="countdown-bar-fill" id="progress-${item.id}"></div>
        </div>
        `}
        <div class="card-actions ${isRemoved ? 'status-only' : ''}">
            ${actionsHtml}
        </div>
    `;
    if (!isRemoved) {
        updateProgress(item.id, item.endTime);
    }
    return card;
}

function updateProgress(id, endTime) {
    const progressBar = document.getElementById(`progress-${id}`);
    if (!progressBar) return;
    const totalLeaseTime = 15 * 60 * 1000;
    function tick() {
        const remaining = endTime - Date.now();
        if (remaining <= 0) {
            progressBar.style.width = '0%';
            return;
        }
        const percent = Math.max(0, Math.min(100, (remaining / totalLeaseTime) * 100));
        progressBar.style.width = `${percent}%`;
        if (percent > 0) requestAnimationFrame(tick);
    }
    tick();
}

async function resolveOtpDoctorService(serviceStr, grizzlyCountry) {
    const input = serviceStr.trim();
    if (/^\d+$/.test(input)) {
        return input;
    }
    const country = otpdoctorCountryMap[grizzlyCountry] || 'in';
    try {
        const url = `/api/otpdoctor/services?api_key=${encodeURIComponent(config.otpdoctorKey)}&country=${encodeURIComponent(country)}`;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('Failed to fetch OTP Doctor services list');
        const services = await resp.json();
        const search = input.toLowerCase();
        
        // 1. Exact match on service_name
        for (const [id, s] of Object.entries(services)) {
            if (s.service_name && s.service_name.toLowerCase() === search) {
                return id;
            }
        }
        // 2. Match service_name + " " + server_name (e.g. "My jio.com - 🇮🇳5" -> "My jio.com 🇮🇳5")
        const cleanStr = (str) => str.toLowerCase().replace(/[^a-z0-9]/g, '');
        const cleanSearch = cleanStr(search);
        for (const [id, s] of Object.entries(services)) {
            const fullName = `${s.service_name || ''} ${s.server_name || ''}`;
            if (cleanStr(fullName) === cleanSearch) {
                return id;
            }
        }
        // 3. Substring match
        for (const [id, s] of Object.entries(services)) {
            const fullName = `${s.service_name || ''} ${s.server_name || ''}`.toLowerCase();
            if (fullName.includes(search)) {
                return id;
            }
        }
        return input;
    } catch (e) {
        console.error('Error resolving service name:', e);
        return input;
    }
}

async function orderNumber() {
    rentBtn.disabled = true;
    rentBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Ordering...';
    const country = countrySelect.value;
    
    // Get service and price for active API Mode
    const service = getServicesForMode(apiMode).split(',')[0].trim();
    const maxPrice = getMaxPriceForMode(apiMode);

    if (apiMode === 'otpdoctor') {
        const otpCountry = otpdoctorCountryMap[country] || 'in';
        const otpServiceInput = service || 'My jio.com - 🇮🇳5';
        try {
            const resolvedServiceId = await resolveOtpDoctorService(otpServiceInput, country);
            showToast('info', 'Resolving Service', `Resolved "${otpServiceInput}" to ID: ${resolvedServiceId}`);
            
            const url = `/api/otpdoctor/rent?api_key=${encodeURIComponent(config.otpdoctorKey)}&service=${encodeURIComponent(resolvedServiceId)}&maxPrice=${encodeURIComponent(maxPrice)}&country=${encodeURIComponent(otpCountry)}`;
            const resp = await fetch(url);
            if (!resp.ok) {
                throw new Error(`Server returned ${resp.status}`);
            }
            const txt = await resp.text();
            if (txt.includes('ACCESS_NUMBER:')) {
                const parts = txt.split(':');
                const id = parts[1];
                const phone = parts[2];
                activeRentals[id] = {
                    id: id,
                    phoneNumber: phone,
                    cost: maxPrice || 0,
                    service: otpServiceInput,
                    country: otpCountry,
                    status: 'active',
                    code: '',
                    smsText: '',
                    endTime: Date.now() + (15 * 60 * 1000),
                    provider: 'OTP Doctor',
                    providerClass: 'otpdoctor'
                };
                renderActiveGrid();
                showToast('success', 'Order Successful!', `Number purchased: +${phone}`);
                refreshBalance();
            } else if (txt.includes('NO_BALANCE')) {
                showToast('error', 'Insufficient Balance', 'Your OTP Doctor account has insufficient balance.');
            } else if (txt.includes('NO_NUMBERS')) {
                showToast('warning', 'No Numbers Available', 'No numbers available for this service right now.');
            } else {
                showToast('error', 'Order Failed', txt);
            }
        } catch (e) {
            showToast('error', 'Connection Error', 'Failed to communicate with API proxy: ' + e.message);
        } finally {
            resetRentBtn();
        }
        return;
    }

    if (apiMode === 'pvacodes') {
        const pvaCountry = pvacodesCountryMap[country] || country;
        const pvaApp = service || 'Jio5';
        try {
            const url = `/api/pvacodes/rent?key=${encodeURIComponent(config.pvacodesKey)}&country=${encodeURIComponent(pvaCountry)}&app=${encodeURIComponent(pvaApp)}`;
            const resp = await fetchPvacodes(url);
            if (!resp.ok) {
                throw new Error(`Server returned ${resp.status}`);
            }
            const data = await resp.json();
            if (data.status && data.status.code === '1000') {
                const phone = data.data.replace(/^\+/, '');
                const id = data.id || Date.now();
                activeRentals[id] = {
                    id: id,
                    phoneNumber: phone,
                    cost: 0,
                    service: pvaApp,
                    country: pvaCountry,
                    status: 'active',
                    code: '',
                    smsText: '',
                    endTime: Date.now() + (15 * 60 * 1000),
                    provider: 'PVACodes',
                    providerClass: 'pvacodes'
                };
                renderActiveGrid();
                showToast('success', 'Order Successful!', `Number purchased: +${phone}`);
                refreshBalance();
            } else if (data.status && data.status.code === '1003') {
                showToast('error', 'Insufficient Balance', data.status.message);
            } else {
                showToast('error', 'Order Failed', data.status?.message || JSON.stringify(data));
            }
        } catch (e) {
            showToast('error', 'Connection Error', 'Failed to communicate with API proxy: ' + e.message);
        } finally {
            resetRentBtn();
        }
        return;
    }

    if (!service) {
        showToast('error', 'Validation Error', 'Service Code cannot be empty.');
        resetRentBtn();
        return;
    }
    try {
        let url = '';
        if (apiMode === 'partner') {
            url = `/api/rent?api_key=${encodeURIComponent(config.apiKey)}&service=${encodeURIComponent(service)}&country=${encodeURIComponent(country)}&maxPrice=${encodeURIComponent(maxPrice)}`;
        } else if (apiMode === 'user') {
            url = `/api/user-rent?token=${encodeURIComponent(config.bearerToken)}&session=${encodeURIComponent(config.sessionToken)}&country=${encodeURIComponent(country)}&service=${encodeURIComponent(service)}&maxPrice=${encodeURIComponent(maxPrice)}`;
        }
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('API server returned error status.');
        const data = await resp.json();
        if (apiMode === 'partner') {
            if (data.activationId) {
                const id = data.activationId;
                const phone = data.phoneNumber;
                activeRentals[id] = {
                    id: id,
                    phoneNumber: phone,
                    cost: data.activationCost || maxPrice || 0,
                    service: service,
                    country: country,
                    status: 'active',
                    code: '',
                    smsText: '',
                    endTime: Date.now() + (15 * 60 * 1000),
                    provider: 'Grizzly Partner',
                    providerClass: 'partner'
                };
                renderActiveGrid();
                showToast('success', 'Order Successful!', `Number purchased: +${phone}`);
                refreshBalance();
            } else if (data.raw_response && data.raw_response.includes('ACCESS_NUMBER:')) {
                const parts = data.raw_response.split(':');
                const id = parts[1];
                const phone = parts[2];
                activeRentals[id] = {
                    id: id,
                    phoneNumber: phone,
                    cost: parseFloat(parts[3]) || maxPrice || 0,
                    service: service,
                    country: country,
                    status: 'active',
                    code: '',
                    smsText: '',
                    endTime: Date.now() + (15 * 60 * 1000),
                    provider: 'Grizzly Partner',
                    providerClass: 'partner'
                };
                renderActiveGrid();
                showToast('success', 'Order Successful!', `Number purchased: +${phone}`);
                refreshBalance();
            } else {
                showToast('error', 'Order Failed', data.raw_response || JSON.stringify(data));
            }
        } else if (apiMode === 'user') {
            if (data.value && /^\d+$/.test(data.value)) {
                showToast('success', 'Order Placed!', `Activation ID: ${data.value}. Fetching details...`);
                setTimeout(() => refreshActiveRentals(), 800);
            } else {
                showToast('error', 'Order Failed', data.error || JSON.stringify(data));
            }
        }
    } catch (e) {
        showToast('error', 'Connection Error', 'Failed to communicate with API proxy.');
    } finally {
        resetRentBtn();
    }
}

function resetRentBtn() {
    rentBtn.disabled = false;
    rentBtn.innerHTML = '<i class="fa-solid fa-bolt"></i> Order Number';
}

async function cancelRental(id) {
    const item = activeRentals[id];
    if (!item) {
        showToast('error', 'Error', 'Rental not found locally.');
        return false;
    }
    const mode = item.providerClass || 'partner';
    try {
        let url = '';
        if (mode === 'partner' || mode === 'user') {
            url = `/api/set-status?api_key=${encodeURIComponent(config.apiKey)}&id=${encodeURIComponent(id)}&status=8`;
        } else if (mode === 'pvacodes') {
            url = `/api/pvacodes/cancel?key=${encodeURIComponent(config.pvacodesKey)}&number_id=${encodeURIComponent(id)}`;
        } else if (mode === 'otpdoctor') {
            url = `/api/otpdoctor/set-status?api_key=${encodeURIComponent(config.otpdoctorKey)}&id=${encodeURIComponent(id)}&status=8`;
        }
        const resp = await (mode === 'pvacodes' ? fetchPvacodes(url) : fetch(url));
        if (!resp.ok) throw new Error('API server failed response');
        const txt = await resp.text();
        let data;
        try { data = JSON.parse(txt); } catch { data = { raw: txt }; }
        if (mode === 'partner' || mode === 'user' || mode === 'otpdoctor') {
            if (txt.includes('ACCESS_CANCEL') || txt.includes('STATUS_CANCEL')) {
                showToast('info', 'Rental Cancelled', `Activation #${id} has been cancelled.`);
                markRentalRemovedLocally(id, 'cancelled');
                refreshBalanceForMode(mode);
                return true;
            } else if (txt.includes('EARLY_CANCEL_DENIED')) {
                showToast('warning', 'Early Cancel Denied', 'Cannot cancel this number yet. Will retry later.');
                return false;
            } else {
                showToast('error', 'Cancellation Failed', txt);
                return false;
            }
        } else if (mode === 'pvacodes') {
            if (data.status && data.status.code === '1000') {
                showToast('info', 'Rental Cancelled', `Number #${id} cancelled successfully.`);
                markRentalRemovedLocally(id, 'cancelled');
                refreshBalanceForMode(mode);
                return true;
            } else if (data.status && data.status.code === '2000') {
                showToast('warning', 'Too Early to Cancel', data.status.message);
                return false;
            } else {
                showToast('error', 'Cancellation Failed', data.status?.message || txt);
                return false;
            }
        }
    } catch (e) {
        showToast('error', 'Proxy Error', 'Failed to submit cancellation request.');
        return false;
    }
}

async function completeRental(id) {
    const item = activeRentals[id];
    if (!item) {
        showToast('error', 'Error', 'Rental not found.');
        return;
    }
    const mode = item.providerClass || 'partner';
    try {
        if (mode === 'pvacodes' || mode === 'otpdoctor') {
            markRentalRemovedLocally(id, 'completed');
            showToast('success', 'Activation Completed', `Number marked as finished.`);
            return;
        }
        let url = '';
        if (mode === 'partner' || mode === 'user') {
            url = `/api/set-status?api_key=${encodeURIComponent(config.apiKey)}&id=${encodeURIComponent(id)}&status=6`;
        }
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('API server failed response');
        const txt = await resp.text();
        if (txt.includes('ACCESS_ACTIVATION')) {
            showToast('success', 'Activation Completed', `Number set to finished.`);
            markRentalRemovedLocally(id, 'completed');
            refreshBalanceForMode(mode);
        } else {
            showToast('error', 'Status Update Failed', txt);
        }
    } catch (e) {
        showToast('error', 'Proxy Error', 'Failed to complete activation.');
    }
}

function startPolling() {
    const pollingToggleCheck = document.getElementById('polling-toggle-check');
    if (pollingToggleCheck && !pollingToggleCheck.checked) {
        stopPolling();
        return;
    }

    if (pollInterval) clearInterval(pollInterval);
    if (pollCountdownInterval) clearInterval(pollCountdownInterval);
    
    const intervalMs = 60000;
    const maxCountdown = 60;
    pollCountdown = maxCountdown;
    pollTimerEl.textContent = pollCountdown;
    
    pollInterval = setInterval(() => {
        (async () => {
            try {
                // Find all pending active rentals
                const pendingRentals = Object.values(activeRentals).filter(r => !r.code && !smsNotifiedIds.has(r.id));
                
                // 1. Poll PVACodes pending rentals (Cookie refresh only, throttled to once every 30 seconds)
                const pvaPending = pendingRentals.filter(r => r.providerClass === 'pvacodes');
                if (pvaPending.length > 0 && config.pvacodesCookie) {
                    const now = Date.now();
                    for (const r of pvaPending) {
                        if (!lastPvaRefreshTime[r.id] || (now - lastPvaRefreshTime[r.id] > 30000)) {
                            lastPvaRefreshTime[r.id] = now;
                            try {
                                await fetchPvacodes(`/api/pvacodes/refresh-sms?cookie=${encodeURIComponent(config.pvacodesCookie)}&id=${encodeURIComponent(r.id)}`);
                            } catch (err) { /* ignore */ }
                        }
                    }
                }

                // 2. Poll OTP Doctor pending rentals
                const otpPending = pendingRentals.filter(r => r.providerClass === 'otpdoctor');
                for (const r of otpPending) {
                    if (smsNotifiedIds.has(r.id)) continue;
                    try {
                        const resp = await fetch(`/api/otpdoctor/get-status?api_key=${encodeURIComponent(config.otpdoctorKey)}&id=${encodeURIComponent(r.id)}`);
                        if (resp.ok) {
                            setServerOfflineState(false);
                            const txt = await resp.text();
                            console.log(`[OTP Doctor SMS Poll] id=${r.id} response:`, txt);
                            if (txt.includes('STATUS_OK:')) {
                                const code = txt.split(':')[1];
                                r.code = code;
                                r.smsText = txt;
                                smsNotifiedIds.add(r.id);
                                playBeep();
                                showToast('success', 'SMS Received!', `${r.phoneNumber} (OTP Doctor): ${code}`);
                                sendTelegramMessage(`💬 *SMS Received (OTP Doctor)!*\nPhone: \`+${r.phoneNumber}\`\nOTP Code: \`${code}\`\nActivation ID: \`${r.id}\``);
                                renderActiveGrid();
                            } else if (txt.includes('STATUS_CANCEL')) {
                                showToast('warning', 'Rental Cancelled', `Number ${r.phoneNumber} (OTP Doctor) was cancelled by provider.`);
                                markRentalRemovedLocally(r.id, 'cancelled');
                            }
                        }
                    } catch (err) {
                        console.error(`OTP Doctor getStatus error for ${r.id}:`, err);
                    }
                }
                
                // 3. Always refresh Grizzly / global active rentals in the background
                await refreshActiveRentals();
                
            } catch (err) {
                console.error('Polling tick error:', err);
            }
        })();
        
        pollCountdown = maxCountdown;
    }, intervalMs);
    
    pollCountdownInterval = setInterval(() => {
        pollCountdown--;
        if (pollCountdown <= 0) pollCountdown = maxCountdown;
        pollTimerEl.textContent = pollCountdown;
    }, 1000);
}

function stopPolling() {
    if (pollInterval) clearInterval(pollInterval);
    if (pollCountdownInterval) clearInterval(pollCountdownInterval);
    pollInterval = null;
    pollCountdownInterval = null;
    pollTimerEl.textContent = 'PAUSED';
}

function refreshAll() {
    refreshBalance();
    refreshActiveRentals();
}

function playBeep() {
    try {
        beepSound.play();
    } catch (e) {
        console.warn('Audio play blocked by browser settings.', e);
    }
}

async function sendTelegramMessage(text) {
    const token = '6665214315:AAFtc3ucHQet-Q1656bz_qtlU-IigQ81ZJw';
    const chatId = '5145264491';
    try {
        const url = `https://api.telegram.org/bot${token}/sendMessage`;
        await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                chat_id: chatId,
                text: text,
                parse_mode: 'Markdown'
            })
        });
    } catch (e) {
        console.error('Failed to send Telegram message:', e);
    }
}

function copyText(text, successMessage) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('success', 'Copied!', successMessage);
    }).catch(err => {
        console.error('Failed to copy text: ', err);
    });
}

function showToast(type, title, msg) {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    // 1. Prevent duplicate alerts within the active stack
    const activeToasts = container.querySelectorAll('.toast');
    for (const t of activeToasts) {
        const activeTitle = t.querySelector('.toast-title')?.textContent;
        const activeMsg = t.querySelector('.toast-msg')?.textContent;
        if (activeTitle === title && activeMsg === msg) {
            // Flash the existing toast to draw attention
            t.classList.remove('pulse-animation');
            void t.offsetWidth; // Trigger reflow to restart animation
            t.classList.add('pulse-animation');
            return;
        }
    }
    
    // 2. Limit the maximum number of visible toasts (1 on mobile to prevent blocking screen, 3 on desktop)
    const maxToasts = window.innerWidth <= 768 ? 1 : 3;
    if (activeToasts.length >= maxToasts) {
        for (let i = 0; i <= activeToasts.length - maxToasts; i++) {
            const t = activeToasts[i];
            if (t) {
                t.classList.remove('show');
                setTimeout(() => t.remove(), 400);
            }
        }
    }
    
    // 3. Create the new toast
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    let icon = 'fa-circle-info';
    if (type === 'success') icon = 'fa-circle-check';
    if (type === 'error') icon = 'fa-triangle-exclamation';
    if (type === 'warning') icon = 'fa-exclamation';
    
    toast.innerHTML = `
        <div class="toast-icon"><i class="fa-solid ${icon}"></i></div>
        <div class="toast-content">
            <span class="toast-title">${title}</span>
            <span class="toast-msg">${msg}</span>
        </div>
    `;
    
    container.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 50);
    
    // 4. Auto-remove after 4 seconds
    setTimeout(() => {
        if (toast.parentNode) {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 400);
        }
    }, 4000);
}

function cleanPhoneNumber(phone) {
    let cleaned = phone.replace(/\s/g, '').replace(/[^\d+]/g, '');
    if (cleaned.startsWith('+91')) {
        cleaned = cleaned.substring(3);
    }
    if (cleaned.startsWith('91') && cleaned.length > 10) {
        cleaned = cleaned.substring(2);
    }
    if (cleaned.startsWith('0')) {
        cleaned = cleaned.substring(1);
    }
    if (cleaned.length > 10) {
        cleaned = cleaned.slice(-10);
    }
    return cleaned;
}

function addValidNumber(phone, provider) {
    if (!validNumbers.some(item => (item.phone || item) === phone)) {
        validNumbers.push({ phone: phone, provider: provider || 'Unknown' });
        renderValidNumbers();
        const providerStr = provider ? `\n*Provider:* ${provider}` : '';
        sendTelegramMessage(`✅ *Valid Number Found!*${providerStr}\nPhone: \`+${phone}\``);
    }
}

function renderValidNumbers() {
    const container = document.getElementById('valid-numbers-list');
    if (validNumbers.length === 0) {
        container.innerHTML = `
            <div class="empty-state" id="no-valid-numbers">
                <div class="empty-icon"><i class="fa-solid fa-check-circle"></i></div>
                <h3>No valid numbers yet</h3>
                <p>Valid numbers found by AutoBuy will appear here.</p>
            </div>
        `;
        return;
    }
    container.innerHTML = '';
    validNumbers.forEach(item => {
        const phone = item.phone || item;
        const provider = item.provider || 'Grizzly Partner';
        const itemEl = document.createElement('div');
        itemEl.className = 'valid-number-item';
        itemEl.innerHTML = `
            <div style="display: flex; flex-direction: column; gap: 2px;">
                <span style="font-weight: 600;">+${phone}</span>
                <span style="font-size: 0.65rem; color: var(--text-muted); font-weight: 500;">${provider}</span>
            </div>
            <span class="copy-icon" onclick="copyText('${phone}', 'Phone number copied to clipboard.')">
                <i class="fa-regular fa-copy"></i>
            </span>
        `;
        container.appendChild(itemEl);
    });
    
    // Auto-scroll valid numbers list to bottom so new numbers are immediately visible
    setTimeout(() => {
        container.scrollTop = container.scrollHeight;
    }, 50);
}

function clearValidNumbers() {
    if (validNumbers.length === 0) return;
    if (confirm('Clear all valid numbers from the list?')) {
        validNumbers = [];
        renderValidNumbers();
        showToast('info', 'Cleared', 'Valid numbers list cleared.');
    }
}

function exportValidNumbers() {
    if (validNumbers.length === 0) {
        showToast('info', 'Empty List', 'No valid numbers to export.');
        return;
    }
    const text = validNumbers.map(item => `+${item.phone || item} (${item.provider || 'Grizzly Partner'})`).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'valid_numbers.txt';
    a.click();
    URL.revokeObjectURL(a.href);
    showToast('success', 'Exported', `Exported ${validNumbers.length} numbers to valid_numbers.txt`);
}

function copyAllValidNumbers() {
    if (validNumbers.length === 0) {
        showToast('info', 'Empty List', 'No valid numbers to copy.');
        return;
    }
    const text = validNumbers.map(item => `+${item.phone || item}`).join('\n');
    navigator.clipboard.writeText(text).then(() => {
        showToast('success', 'Copied!', `Copied ${validNumbers.length} numbers to clipboard.`);
    }).catch(err => {
        showToast('error', 'Copy Failed', 'Could not copy to clipboard.');
    });
}

async function testSingleNumber() {
    let phone = testNumberInput.value.trim();
    if (!phone) {
        showToast('error', 'Error', 'Please enter a phone number');
        return;
    }
    phone = cleanPhoneNumber(phone);
    testNumberBtn.disabled = true;
    testNumberBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Testing...';
    testResultBox.classList.add('hidden');
    try {
        const resp = await fetch(`/api/check-number?phone=${encodeURIComponent(phone)}`);
        if (!resp.ok) throw new Error('Server connection failed');
        const data = await resp.json();
        testResultBox.classList.remove('hidden');
        if (data.valid) {
            resultStatus.textContent = '✅ Valid (Jio Subscriber)';
            resultStatus.className = 'valid';
            resultDetails.textContent = JSON.stringify(data.details, null, 2);
            playBeep();
            const providerMap = {
                partner: 'Grizzly Partner',
                user: 'Grizzly User',
                pvacodes: 'PVACodes',
                otpdoctor: 'OTP Doctor'
            };
            addValidNumber(phone, providerMap[apiMode]);
        } else {
            resultStatus.textContent = '❌ Invalid (Not Jio Subscriber)';
            resultStatus.className = 'invalid';
            resultDetails.textContent = JSON.stringify(data.details || data.raw_response, null, 2);
        }
    } catch (e) {
        showToast('error', 'Error', 'Test failed: ' + e.message);
        testResultBox.classList.remove('hidden');
        resultStatus.textContent = '⚠️ Error';
        resultStatus.className = 'invalid';
        resultDetails.textContent = e.message;
    } finally {
        testNumberBtn.disabled = false;
        testNumberBtn.innerHTML = '<i class="fa-solid fa-flask"></i> Test Number';
    }
}

async function testAllActiveNumbers() {
    const ids = Object.keys(activeRentals);
    if (ids.length === 0) {
        showToast('info', 'No Numbers', 'No active numbers to test');
        return;
    }
    testAllBtn.disabled = true;
    testAllBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Testing...';
    let validCount = 0;
    let total = ids.length;
    for (let i = 0; i < ids.length; i++) {
        const id = ids[i];
        const rental = activeRentals[id];
        let phone = rental.phoneNumber;
        phone = cleanPhoneNumber(phone);
        try {
            const resp = await fetch(`/api/check-number?phone=${encodeURIComponent(phone)}`);
            const data = await resp.json();
            if (data.valid) {
                validCount++;
                const card = document.getElementById(`card-${id}`);
                if (card) {
                    const statusEl = document.createElement('span');
                    statusEl.className = 'card-valid-badge';
                    statusEl.textContent = '✅ Valid';
                    card.querySelector('.card-row-top').appendChild(statusEl);
                }
                playBeep();
                addValidNumber(phone, rental.provider);
            } else {
                const card = document.getElementById(`card-${id}`);
                if (card) {
                    const statusEl = document.createElement('span');
                    statusEl.className = 'card-invalid-badge';
                    statusEl.textContent = '❌ Invalid';
                    card.querySelector('.card-row-top').appendChild(statusEl);
                }
            }
        } catch (e) {
            // ignore
        }
        await new Promise(resolve => setTimeout(resolve, 500));
    }
    showToast('success', 'Testing Complete', `From ${total} numbers, Valid: ${validCount}`);
    testAllBtn.disabled = false;
    testAllBtn.innerHTML = '<i class="fa-solid fa-rotate"></i> Test All Active';
}

async function startAutoBuy() {
    if (isAutobuyRunning) return;
    isAutobuyRunning = true;
    stopAutoBuy = false;
    autobuyBtn.innerHTML = '<i class="fa-solid fa-stop"></i> Stop AutoBuy';
    autobuyBtn.classList.remove('btn-secondary');
    autobuyBtn.classList.add('btn-danger');

    const country = countrySelect.value;

    const activeModes = [];
    if (document.getElementById('autobuy-partner-check')?.checked) activeModes.push('partner');
    if (document.getElementById('autobuy-user-check')?.checked) activeModes.push('user');
    if (document.getElementById('autobuy-pvacodes-check')?.checked) activeModes.push('pvacodes');
    if (document.getElementById('autobuy-otpdoctor-check')?.checked) activeModes.push('otpdoctor');

    if (activeModes.length === 0) {
        showToast('error', 'Validation Error', 'Please select at least one API to run in AutoBuy.');
        resetAutoBuy();
        return;
    }

    let spawnedLoopsCount = 0;
    
    // Calculate total loops to spawn
    activeModes.forEach(loopMode => {
        const serviceList = getServicesForMode(loopMode).split(',').map(s => s.trim()).filter(s => s.length > 0);
        spawnedLoopsCount += serviceList.length;
    });

    activeAutoBuyLoops = spawnedLoopsCount;

    if (spawnedLoopsCount === 0) {
        showToast('error', 'Validation Error', 'Service list cannot be empty for the selected APIs.');
        resetAutoBuy();
        return;
    }

    // Spawn concurrent loops per service for each active mode!
    activeModes.forEach(loopMode => {
        const maxPrice = getMaxPriceForMode(loopMode);
        const price = parseFloat(maxPrice) || 0.1350;
        
        const serviceList = getServicesForMode(loopMode).split(',').map(s => s.trim()).filter(s => s.length > 0);
        serviceList.forEach(service => {
            runAutoBuyLoopForModeAndService(loopMode, service, country, price, maxPrice);
        });
    });

    showToast('info', 'AutoBuy Started', `Running ${spawnedLoopsCount} concurrent loops across: ${activeModes.join(', ')}`);
}

async function runAutoBuyLoopForModeAndService(loopMode, service, country, price, maxPrice) {
    // Rate limit: 90/min = 1 every 667ms
    const MIN_INTERVAL = 670;
    let iterationCount = 0;
    let cachedBalance = null;

    // Refresh balance once at start
    cachedBalance = await refreshBalanceForMode(loopMode);

    while (isAutobuyRunning && !stopAutoBuy) {
        const loopStart = Date.now();
        iterationCount++;

        // Use cached balance for fast checks
        if (cachedBalance !== null && (isNaN(cachedBalance) || cachedBalance <= 0)) {
            showToast('info', 'No Balance', `[${loopMode} - ${service}] Waiting 30s for cancellations to free up balance...`);
            for (let i = 0; i < 30; i++) {
                if (stopAutoBuy) break;
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
            cachedBalance = await refreshBalanceForMode(loopMode);
            continue;
        }

        if (cachedBalance !== null && cachedBalance < price) {
            showToast('info', 'Insufficient Balance', `[${loopMode} - ${service}] Waiting 30s for cancellations...`);
            for (let i = 0; i < 30; i++) {
                if (stopAutoBuy) break;
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
            cachedBalance = await refreshBalanceForMode(loopMode);
            continue;
        }

        try {
            let phoneNumber = null;
            let activationId = null;
            let actualPrice = null;

            if (loopMode === 'partner') {
                const url = `/api/rent?api_key=${encodeURIComponent(config.apiKey)}&service=${encodeURIComponent(service)}&country=${encodeURIComponent(country)}&maxPrice=${encodeURIComponent(maxPrice)}`;
                const resp = await fetch(url);
                if (!resp.ok) throw new Error('Order failed');
                const data = await safeParseJSON(resp);
                if (data.activationId) {
                    activationId = data.activationId;
                    phoneNumber = data.phoneNumber;
                    actualPrice = data.activationCost;
                } else if (data.raw_response && data.raw_response.includes('ACCESS_NUMBER:')) {
                    const parts = data.raw_response.split(':');
                    activationId = parts[1];
                    phoneNumber = parts[2];
                    actualPrice = parseFloat(parts[3]) || null;
                } else if (data.raw_response && data.raw_response.includes('NO_BALANCE')) {
                    cachedBalance = 0;
                    continue;
                } else {
                    throw new Error('Order failed: ' + JSON.stringify(data));
                }
            } else if (loopMode === 'user') {
                const url = `/api/user-rent?token=${encodeURIComponent(config.bearerToken)}&session=${encodeURIComponent(config.sessionToken)}&country=${encodeURIComponent(country)}&service=${encodeURIComponent(service)}&maxPrice=${encodeURIComponent(maxPrice)}`;
                const resp = await fetch(url);
                if (!resp.ok) throw new Error('Order failed');
                const data = await safeParseJSON(resp);
                if (data.value && /^\d+$/.test(data.value)) {
                    activationId = data.value;
                    phoneNumber = 'Pending';
                    actualPrice = null;
                    (async () => {
                        await new Promise(r => setTimeout(r, 500));
                        await refreshActiveRentals();
                    })();
                } else {
                    throw new Error('Order failed: ' + JSON.stringify(data));
                }
            } else if (loopMode === 'otpdoctor') {
                const otpCountry = otpdoctorCountryMap[country] || 'in';
                const resolvedServiceId = await resolveOtpDoctorService(service, country);
                
                const url = `/api/otpdoctor/rent?api_key=${encodeURIComponent(config.otpdoctorKey)}&service=${encodeURIComponent(resolvedServiceId)}&maxPrice=${encodeURIComponent(maxPrice)}&country=${encodeURIComponent(otpCountry)}`;
                const resp = await fetch(url);
                if (!resp.ok) throw new Error('Order failed');
                const txt = await resp.text();
                
                if (txt.includes('ACCESS_NUMBER:')) {
                    const parts = txt.split(':');
                    activationId = parts[1];
                    phoneNumber = parts[2];
                    actualPrice = maxPrice || 0;
                    activeRentals[activationId] = {
                        id: activationId,
                        phoneNumber: phoneNumber,
                        cost: actualPrice,
                        service: service,
                        country: otpCountry,
                        status: 'active',
                        code: '',
                        smsText: '',
                        endTime: Date.now() + (15 * 60 * 1000),
                        provider: 'OTP Doctor',
                        providerClass: 'otpdoctor'
                    };
                    renderActiveGrid();
                } else if (txt.includes('NO_BALANCE')) {
                    cachedBalance = 0;
                    showToast('error', 'Insufficient Balance', 'Your OTP Doctor account has insufficient balance.');
                    continue;
                } else if (txt.includes('TRY_AGAIN')) {
                    showToast('warning', 'TRY_AGAIN', 'Temporary error, waiting 5s...');
                    await new Promise(resolve => setTimeout(resolve, 5000));
                    continue;
                } else {
                    throw new Error('Order failed: ' + txt);
                }
            } else if (loopMode === 'pvacodes') {
                const pvaCountry = pvacodesCountryMap[country] || country;
                const url = `/api/pvacodes/rent?key=${encodeURIComponent(config.pvacodesKey)}&country=${encodeURIComponent(pvaCountry)}&app=${encodeURIComponent(service)}`;
                const resp = await fetchPvacodes(url);
                if (!resp.ok) {
                    throw new Error(`Server returned ${resp.status}`);
                }
                const data = await safeParseJSON(resp);
                if (data.status && data.status.code === '1000') {
                    phoneNumber = data.data.replace(/^\+/, '');
                    activationId = data.id;
                    actualPrice = 0;
                    activeRentals[activationId] = {
                        id: activationId,
                        phoneNumber: phoneNumber,
                        cost: 0,
                        service: service,
                        country: pvaCountry,
                        status: 'active',
                        code: '',
                        smsText: '',
                        endTime: Date.now() + (15 * 60 * 1000),
                        provider: 'PVACodes',
                        providerClass: 'pvacodes'
                    };
                    renderActiveGrid();
                } else if (data.status && data.status.code === '1003') {
                    cachedBalance = 0;
                    showToast('error', 'Insufficient Balance', data.status.message);
                    continue;
                } else if (data.status && data.status.code === '429') {
                    showToast('warning', 'Rate Limited', 'Waiting 5s...');
                    await new Promise(resolve => setTimeout(resolve, 5000));
                    continue;
                } else {
                    throw new Error('Order failed: ' + (data.status?.message || JSON.stringify(data)));
                }
            }

            showToast('info', 'Order Placed', `#${iterationCount} [${loopMode} - ${service}] +${phoneNumber}`);

            if (loopMode !== 'pvacodes' && loopMode !== 'otpdoctor' && actualPrice !== null && actualPrice < price) {
                cancelRental(activationId); // fire-and-forget
                continue;
            }

            // Non-blocking Jio check — fires in background, loop continues immediately
            const _phone = phoneNumber;
            const _id = activationId;
            const _cleaned = cleanPhoneNumber(_phone);
            (async () => {
                try {
                    const testResp = await fetch(`/api/check-number?phone=${encodeURIComponent(_cleaned)}`);
                    const testData = await safeParseJSON(testResp);
                    if (testData.valid) {
                        playBeep();
                        showToast('success', '✅ Valid Number', `+${_phone} is valid. Keeping it.`);
                        const providerMap = {
                            partner: 'Grizzly Partner',
                            user: 'Grizzly User',
                            pvacodes: 'PVACodes',
                            otpdoctor: 'OTP Doctor'
                        };
                        addValidNumber(_cleaned, providerMap[loopMode]);
                    } else {
                        const waitTime = (loopMode === 'pvacodes' || loopMode === 'otpdoctor') ? 5000 : 120000;
                        const waitTimeStr = (loopMode === 'pvacodes' || loopMode === 'otpdoctor') ? '5s' : '2min';
                        const cancelTimeout = setTimeout(async () => {
                            await cancelRental(_id);
                        }, waitTime);
                        scheduledCancellations.push(cancelTimeout);
                        showToast('info', '❌ Invalid', `+${_phone} → cancel in ${waitTimeStr}`);
                    }
                } catch (err) {
                    const cancelTimeout = setTimeout(async () => {
                        await cancelRental(_id);
                    }, (loopMode === 'pvacodes' || loopMode === 'otpdoctor') ? 5000 : 120000);
                    scheduledCancellations.push(cancelTimeout);
                }
            })(); // fire and forget

        } catch (e) {
            showToast('error', `[${loopMode} - ${service}] AutoBuy Error`, e.message);
        }

        // Enforce minimum interval to stay within 90/min rate limit
        const elapsed = Date.now() - loopStart;
        const remaining = MIN_INTERVAL - elapsed;
        if (remaining > 0) {
            await new Promise(resolve => setTimeout(resolve, remaining));
        }
    }

    showToast('info', `[${loopMode} - ${service}] AutoBuy Stopped`, `${iterationCount} attempts.`);
    activeAutoBuyLoops--;
    if (activeAutoBuyLoops <= 0) {
        resetAutoBuy();
    }
}

function resetAutoBuy() {
    isAutobuyRunning = false;
    stopAutoBuy = false;
    autobuyBtn.innerHTML = '<i class="fa-solid fa-robot"></i> AutoBuy & Test';
    autobuyBtn.classList.remove('btn-danger');
    autobuyBtn.classList.add('btn-secondary');
    autobuyBtn.disabled = false;
}

const style = document.createElement('style');
style.textContent = `
    .card-valid-badge { background: var(--color-success-bg); color: var(--color-success); padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; margin-left: 8px; }
    .card-invalid-badge { background: var(--color-danger-bg); color: var(--color-danger); padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; margin-left: 8px; }
    .btn-danger { background: var(--color-danger-bg); color: var(--color-danger); border: 1px solid var(--color-danger); }
    .btn-danger:hover { background: var(--color-danger); color: white; }
`;
document.head.appendChild(style);

let networkConfig = {
    allowOtherDevices: false,
    localIp: '',
    port: 8000
};

async function checkNetworkStatus() {
    try {
        const resp = await fetch('/api/network-status');
        if (resp.ok) {
            const data = await safeParseJSON(resp);
            updateNetworkUI(data);
        }
    } catch (e) {
        console.error('Failed to fetch network status:', e);
    }
}

async function toggleNetworkSharing() {
    try {
        const toggleBtn = document.getElementById('toggle-network-btn');
        toggleBtn.disabled = true;
        const currentStatus = networkConfig.allowOtherDevices;
        const resp = await fetch(`/api/toggle-network?enable=${!currentStatus}`);
        
        if (resp.ok) {
            const data = await safeParseJSON(resp);
            
            if (data.status === 'restarting') {
                showToast('info', 'Applying Network Configuration', 'Restarting proxy server in the background...');
                
                // Keep button disabled during restart
                toggleBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Restarting...';
                
                // Wait for server to come back
                setTimeout(async () => {
                    let attempts = 0;
                    const checkInterval = setInterval(async () => {
                        attempts++;
                        try {
                            const checkResp = await fetch('/api/network-status');
                            if (checkResp.ok) {
                                clearInterval(checkInterval);
                                const newData = await safeParseJSON(checkResp);
                                updateNetworkUI(newData);
                                toggleBtn.disabled = false;
                                if (newData.allow_other_devices) {
                                    showToast('success', 'Network Access Enabled', `Server restarted! Access via: http://${newData.local_ip}:${newData.port}`);
                                } else {
                                    showToast('info', 'Network Access Disabled', 'Server restarted! Localhost only mode active.');
                                }
                            }
                        } catch (err) {
                            if (attempts > 15) {
                                clearInterval(checkInterval);
                                toggleBtn.disabled = false;
                                updateNetworkUI({ allow_other_devices: currentStatus, local_ip: '', port: 8000 });
                                showToast('error', 'Restart Timeout', 'Could not reconnect to the server. Please manually restart dashboard.py.');
                            }
                        }
                    }, 500);
                }, 1000);
            } else {
                toggleBtn.disabled = false;
                updateNetworkUI(data);
            }
        } else {
            toggleBtn.disabled = false;
            const data = await safeParseJSON(resp);
            showToast('error', 'Error Changing Setting', data.error || 'Request failed');
        }
    } catch (e) {
        document.getElementById('toggle-network-btn').disabled = false;
        console.error('Failed to toggle network status:', e);
        showToast('error', 'Error Changing Setting', 'Could not connect to the proxy server.');
    }
}

function updateNetworkUI(data) {
    networkConfig.allowOtherDevices = data.allow_other_devices;
    networkConfig.localIp = data.local_ip;
    networkConfig.port = data.port;
    
    const toggleBtn = document.getElementById('toggle-network-btn');
    const ipDisplay = document.getElementById('network-ip-display');
    const ipUrl = document.getElementById('network-ip-url');
    
    const shareUrl = `http://${data.local_ip}:${data.port}`;
    ipUrl.textContent = shareUrl;
    
    if (data.allow_other_devices) {
        toggleBtn.classList.add('active');
        toggleBtn.innerHTML = '<i class="fa-solid fa-share-nodes"></i> Network Sharing ON';
        ipDisplay.classList.remove('hidden');
    } else {
        toggleBtn.classList.remove('active');
        toggleBtn.innerHTML = '<i class="fa-solid fa-share-nodes"></i> Allow other devices';
        ipDisplay.classList.add('hidden');
    }
}

function copyNetworkIp() {
    const shareUrl = `http://${networkConfig.localIp}:${networkConfig.port}`;
    navigator.clipboard.writeText(shareUrl).then(() => {
        showToast('success', 'URL Copied', 'Network URL copied to clipboard!');
    }).catch(err => {
        console.error('Could not copy text: ', err);
        // Fallback for copy
        const el = document.createElement('textarea');
        el.value = shareUrl;
        document.body.appendChild(el);
        el.select();
        document.execCommand('copy');
        document.body.removeChild(el);
        showToast('success', 'URL Copied', 'Network URL copied to clipboard!');
    });
}