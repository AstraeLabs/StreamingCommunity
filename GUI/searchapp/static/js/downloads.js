/**
  * downloads.js - Handles fetching and rendering of active downloads and download history.
*/

import { formatTime, formatDate, fetchWithTimeout } from './utils.js';

const expandedRows = new Set();
const UPDATE_INTERVAL = 1000;

function normalizeTypeLabel(type, title = '') {
  const raw = String(type || '').toLowerCase();
  if (raw === 'serie' || raw === 'tv' || raw === 'series') return 'Serie';
  if (raw === 'film' || raw === 'movie') return 'Film';
  return title && title.match(/[SE]\d+/i) ? 'Serie' : 'Film';
}

async function fetchDownloadData() {
  try {
    const response = await fetchWithTimeout(window.DOWNLOADS_JSON_URL, {}, 5000);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    console.error('Failed to fetch download data:', error);
    return { active: [], scheduled: [], history: [] };
  }
}

function renderActiveDownloads(downloads) {
  const container = document.getElementById('active-downloads-container');
  const noDownloads = document.getElementById('no-active-downloads');
  
  if (!container || !noDownloads) return;
  
  // Clean up old cards
  const activeIds = new Set(downloads.map(dl => dl.id));
  container.querySelectorAll('.download-card').forEach(card => {
    if (!activeIds.has(card.dataset.id)) card.remove();
  });

  // Toggle empty state
  noDownloads.style.display = downloads.length === 0 ? 'block' : 'none';
  
  downloads.forEach(dl => {
    let card = container.querySelector(`.download-card[data-id="${dl.id}"]`);
    
    if (!card) {
      card = document.createElement('div');
      card.className = 'download-card bg-gradient-to-br from-gray-900/80 to-gray-900/40 backdrop-blur-xl border border-gray-800 rounded-xl sm:rounded-2xl overflow-hidden hover:border-gray-700 transition-all';
      card.dataset.id = dl.id;
      container.appendChild(card);
    }
    
    card.innerHTML = generateDownloadCardHTML(dl);
  });
}

function renderScheduledDownloads(downloads) {
  const container = document.getElementById('scheduled-downloads-container');
  const noDownloads = document.getElementById('no-scheduled-downloads');

  if (!container || !noDownloads) return;

  const scheduledIds = new Set(downloads.map(dl => dl.id));
  container.querySelectorAll('.scheduled-download-card').forEach(card => {
    if (!scheduledIds.has(card.dataset.id)) card.remove();
  });

  noDownloads.style.display = downloads.length === 0 ? 'block' : 'none';

  downloads.forEach(dl => {
    let card = container.querySelector(`.scheduled-download-card[data-id="${dl.id}"]`);
    if (!card) {
      card = document.createElement('div');
      card.className = 'scheduled-download-card bg-gradient-to-br from-gray-900/80 to-gray-900/40 backdrop-blur-xl border border-gray-800 rounded-xl sm:rounded-2xl p-4 sm:p-5';
      card.dataset.id = dl.id;
      container.appendChild(card);
    }

    const typeLabel = normalizeTypeLabel(dl.type, dl.title);
    const scheduleDate = dl.scheduled_at ? formatDate(dl.scheduled_at) : '';
    const seasonLabel = dl.season ? `S${escapeHtml(String(dl.season))}` : '';
    const episodesLabel = dl.episodes && dl.episodes !== '*' ? `E${escapeHtml(String(dl.episodes))}` : (dl.episodes === '*' ? 'Tutti gli episodi' : '');
    const details = [seasonLabel, episodesLabel].filter(Boolean).join(' • ');

    card.innerHTML = `
      <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div class="min-w-0">
          <div class="flex items-center gap-2 mb-2">
            <span class="px-2.5 py-1 bg-amber-500/20 text-amber-300 text-[10px] sm:text-xs font-bold uppercase tracking-wider rounded">
              In attesa
            </span>
            <span class="px-2.5 py-1 bg-white/10 text-gray-300 text-[10px] sm:text-xs font-semibold rounded">
              ${escapeHtml(typeLabel)}
            </span>
            <span class="px-2.5 py-1 bg-white/10 text-gray-400 text-[10px] sm:text-xs rounded">
              ${escapeHtml(dl.site || '')}
            </span>
          </div>
          <p class="text-sm sm:text-base font-semibold text-white truncate">${escapeHtml(dl.title || 'Download')}</p>
          ${details ? `<p class="text-xs text-gray-400 mt-1">${details}</p>` : ''}
        </div>
        <div class="flex items-center gap-3">
          <div class="text-xs text-gray-500 font-mono whitespace-nowrap">
            ${scheduleDate}
          </div>
          <button
            onclick="window.killSeriesQueue('${dl.id}')"
            class="px-3 py-2 bg-red-600/10 hover:bg-red-600 active:bg-red-700 text-red-500 hover:text-white border border-red-600/30 rounded text-[10px] sm:text-xs font-bold transition-all flex items-center justify-center gap-1.5 min-h-[34px]"
            title="Annulla questo download programmato"
          >
            <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
            </svg>
            Cancella
          </button>
        </div>
      </div>
    `;
  });
}

function generateDownloadCardHTML(dl) {
  const isExpanded = expandedRows.has(dl.id);
  const hasTasks = Object.keys(dl.tasks || {}).length > 0;
  
  const typeLabel = normalizeTypeLabel(dl.type, dl.title);
  const isSeries = typeLabel === 'Serie';
  const elapsedSec = Math.floor(Date.now() / 1000 - (dl.start_time || 0));
  const timeStr = formatTime(elapsedSec);

  // Progress color based on percentage
  let progressColor = 'from-blue-600 to-blue-500';
  if (dl.progress > 75) progressColor = 'from-green-600 to-green-500';
  else if (dl.progress > 50) progressColor = 'from-yellow-600 to-yellow-500';
  else if (dl.progress > 25) progressColor = 'from-orange-600 to-orange-500';

  return `
    <div class="p-4 sm:p-6 lg:p-8">
      <!-- Main Download Info -->
      <div class="flex flex-col sm:flex-row gap-4 sm:gap-6">
        <!-- Left: Poster/Icon -->
        <div class="flex-shrink-0 mx-auto sm:mx-0">
          <div class="w-20 h-30 sm:w-24 sm:h-36 lg:w-32 lg:h-48 bg-gradient-to-br from-red-600 to-purple-600 rounded-lg flex items-center justify-center shadow-2xl shadow-red-600/30">
            <svg class="w-10 h-10 sm:w-12 sm:h-12 lg:w-16 lg:h-16 text-white/90" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z"/>
            </svg>
          </div>
        </div>

        <!-- Center: Info & Progress -->
        <div class="flex-1 min-w-0">
          <!-- Title & Type -->
          <div class="mb-4">
            <div class="flex flex-wrap items-center justify-center sm:justify-start gap-2 mb-3">
              <span class="px-2.5 py-1 sm:px-3 bg-red-600 text-white text-[10px] sm:text-xs font-bold uppercase tracking-wider rounded">
                ${escapeHtml(typeLabel)}
              </span>
              <span class="px-2.5 py-1 sm:px-3 bg-white/10 backdrop-blur-sm text-gray-300 text-[10px] sm:text-xs font-semibold rounded">
                ${escapeHtml(dl.site)}
              </span>
              ${dl.series_name ? `
              <span class="max-w-full sm:max-w-[260px] px-2.5 py-1 sm:px-3 bg-blue-600/20 border border-blue-500/30 text-blue-200 text-[10px] sm:text-xs font-semibold rounded truncate" title="${escapeHtml(dl.series_name)}">
                ${escapeHtml(dl.series_name)}
              </span>
              ` : ''}
              <button 
                onclick="window.killDownload('${dl.id}')"
                class="w-full sm:w-auto sm:ml-2 px-3 py-2 sm:px-2 sm:py-0.5 bg-red-600/10 hover:bg-red-600 active:bg-red-700 text-red-500 hover:text-white border border-red-600/30 rounded text-xs sm:text-[10px] font-bold transition-all flex items-center justify-center gap-1.5 sm:gap-1 min-h-[36px] sm:min-h-0"
                title="Smetti di scaricare questo singolo processo"
              >
                <svg class="w-4 h-4 sm:w-3 sm:h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                </svg>
                CANCELLA DOWNLOAD CORRENTE
              </button>
              ${isSeries ? `
              <button 
                onclick="window.killAndClearQueue('${dl.id}')"
                class="w-full sm:w-auto sm:ml-1 px-3 py-2 sm:px-2 sm:py-0.5 bg-orange-600/10 hover:bg-orange-600 active:bg-orange-700 text-orange-500 hover:text-white border border-orange-600/30 rounded text-xs sm:text-[10px] font-bold transition-all flex items-center justify-center gap-1.5 sm:gap-1 min-h-[36px] sm:min-h-0"
                title="Cancella questo download e tutti i successivi della serie"
              >
                <svg class="w-4 h-4 sm:w-3 sm:h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                </svg>
                CANCELLA DOWNLOAD CORRENTE E SUCCESSIVI
              </button>
              ` : ''}
            </div>
            <h3 class="text-lg sm:text-xl lg:text-2xl font-bold text-white mb-1 text-center sm:text-left">
              ${escapeHtml(dl.title)}
            </h3>
            <p class="text-xs sm:text-sm text-gray-400 text-center sm:text-left">
              ${escapeHtml(dl.status)} • ${timeStr} trascorsi
            </p>
            ${dl.path ? `
            <p class="text-[10px] sm:text-xs text-gray-400 mt-1 truncate opacity-60 text-center sm:text-left" title="${escapeHtml(dl.path)}">
              ${escapeHtml(dl.path)}
            </p>
            ` : ''}
          </div>

          <!-- Progress Bar -->
          <div class="mb-4">
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs sm:text-sm font-semibold text-gray-300">Progresso</span>
              <span class="text-base sm:text-lg font-bold text-white font-mono">${dl.progress.toFixed(1)}%</span>
            </div>
            <div class="h-2 sm:h-2.5 w-full bg-gray-800 rounded-full overflow-hidden">
              <div class="h-full bg-gradient-to-r ${progressColor} rounded-full transition-all duration-1000 shadow-lg" style="width: ${dl.progress}%"></div>
            </div>
          </div>

          <!-- Stats Grid -->
          <div class="grid grid-cols-2 gap-3 sm:gap-4 mb-4 sm:mb-6">
            <div class="bg-black/30 rounded-lg p-3 sm:p-4">
              <p class="text-[10px] sm:text-xs text-gray-500 uppercase tracking-wider mb-1">Dimensione</p>
              <p class="text-sm sm:text-base font-bold text-white font-mono truncate">${escapeHtml(dl.size)}</p>
            </div>
            <div class="bg-black/30 rounded-lg p-3 sm:p-4">
              <p class="text-[10px] sm:text-xs text-gray-500 uppercase tracking-wider mb-1">Velocità</p>
              <p class="text-sm sm:text-base font-bold text-green-400 font-mono truncate">${escapeHtml(dl.speed)}</p>
            </div>
          </div>

          <!-- Expand Tasks Button -->
          ${hasTasks ? `
          <button 
            onclick="window.toggleTasks('${dl.id}')"
            class="w-full sm:w-auto flex items-center justify-center sm:justify-start gap-2 text-xs sm:text-sm text-gray-400 hover:text-white transition-colors group py-2"
            id="btn-${dl.id}"
          >
            <svg class="w-4 h-4 transition-transform ${isExpanded ? 'rotate-180' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
            </svg>
            <span class="font-semibold">
              ${isExpanded ? 'Nascondi' : 'Mostra'} dettagli flussi (${Object.keys(dl.tasks).length})
            </span>
          </button>
          ` : ''}
        </div>
      </div>

      <!-- Expandable Tasks Section -->
      ${hasTasks ? generateTasksHTML(dl, isExpanded) : ''}
    </div>
  `;
}

function generateTasksHTML(dl, isExpanded) {
  const tasksArray = Object.entries(dl.tasks);
  if (tasksArray.length === 0) return '';
  
  return `
    <div class="tasks-section mt-4 sm:mt-6 pt-4 sm:pt-6 border-t border-gray-700 ${isExpanded ? '' : 'hidden'}" id="tasks-${dl.id}">
      <h4 class="text-xs sm:text-sm font-bold text-gray-400 uppercase tracking-wider mb-3 sm:mb-4 text-center sm:text-left">
        Flussi Dettagliati
      </h4>
      <div class="space-y-2 sm:space-y-3">
        ${tasksArray.map(([name, task]) => `
          <div class="bg-black/40 rounded-lg sm:rounded-xl p-3 sm:p-4 hover:bg-black/50 transition-colors">
            <div class="flex flex-col gap-2 sm:gap-3 mb-2 sm:mb-3">
              <div class="flex-1 min-w-0">
                <h5 class="text-xs sm:text-sm font-semibold text-white truncate" title="${escapeHtml(name)}">
                  ${escapeHtml(name.replace(/_/g, ' '))}
                </h5>
              </div>
              <div class="flex items-center justify-between gap-4 text-xs sm:text-sm">
                <div class="flex items-center gap-1">
                  <span class="text-gray-500">Progresso:</span>
                  <span class="text-blue-400 font-bold">${task.progress.toFixed(1)}%</span>
                </div>
                <div class="flex items-center gap-1">
                  <span class="text-gray-500">Velocità:</span>
                  <span class="text-green-400 font-mono text-xs">${escapeHtml(task.speed)}</span>
                </div>
              </div>
            </div>
            <div class="h-1.5 w-full bg-gray-800 rounded-full overflow-hidden">
              <div class="h-full bg-gradient-to-r from-blue-600 to-blue-400 rounded-full transition-all duration-1000" style="width: ${task.progress}%"></div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `;
}

function renderHistory(history) {
  const body = document.getElementById('history-body');
  if (!body) return;
  
  if (history.length === 0) {
    body.innerHTML = `
      <tr>
        <td colspan="3" class="px-4 sm:px-6 py-8 sm:py-12 text-center">
          <div class="flex flex-col items-center gap-2 sm:gap-3">
            <svg class="w-10 h-10 sm:w-12 sm:h-12 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
            </svg>
            <span class="text-gray-500 font-medium text-sm sm:text-base">Nessun download completato</span>
          </div>
        </td>
      </tr>
    `;
    return;
  }

  body.innerHTML = history.map(item => {
    const date = formatDate(item.end_time);
    const isSuccess = item.status === 'completed';
    
    return `
      <tr class="hover:bg-white/5 transition-colors group">
        <td class="px-4 sm:px-6 py-4 sm:py-5">
          <div class="flex items-center gap-3 sm:gap-4">
            <div class="w-10 h-10 sm:w-12 sm:h-12 flex-shrink-0 bg-gradient-to-br ${isSuccess ? 'from-green-600 to-green-700' : 'from-red-600 to-red-700'} rounded-lg flex items-center justify-center shadow-lg">
              <svg class="w-5 h-5 sm:w-6 sm:h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                ${isSuccess 
                  ? '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M5 13l4 4L19 7"></path>'
                  : '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12"></path>'
                }
              </svg>
            </div>
            <div class="flex-1 min-w-0">
              <h4 class="text-xs sm:text-sm font-semibold text-white mb-0.5 sm:mb-1 truncate group-hover:text-red-400 transition-colors">
                ${escapeHtml(item.title)}
              </h4>
              <p class="text-[10px] sm:text-xs text-gray-500 uppercase tracking-wide">
                ${escapeHtml(item.site)}
              </p>
              ${item.path ? `
              <p class="text-[10px] sm:text-xs text-gray-400 mt-1 truncate border-t border-gray-800 pt-1" title="${escapeHtml(item.path)}">
                <span class="text-gray-600">Percorso:</span> ${escapeHtml(item.path)}
              </p>
              ` : ''}
            </div>
          </div>
        </td>
        <td class="px-4 sm:px-6 py-4 sm:py-5 text-center">
          <span class="inline-flex px-2.5 py-1.5 sm:px-4 sm:py-2 ${isSuccess ? 'bg-green-600' : 'bg-red-600'} text-white text-[10px] sm:text-xs font-bold uppercase tracking-wider rounded shadow-lg">
            ${isSuccess ? 'OK' : 'NO'}
          </span>
        </td>
        <td class="px-4 sm:px-6 py-4 sm:py-5 text-right text-[10px] sm:text-sm text-gray-400 font-mono whitespace-nowrap">
          ${date}
        </td>
      </tr>
    `;
  }).join('');
}

function toggleTasks(id) {
  if (expandedRows.has(id)) {
    expandedRows.delete(id);
  } else {
    expandedRows.add(id);
  }
  
  const btn = document.getElementById(`btn-${id}`);
  const tasks = document.getElementById(`tasks-${id}`);
  
  if (btn && tasks) {
    btn.querySelector('svg').classList.toggle('rotate-180');
    const taskCount = tasks.querySelectorAll('.bg-black\\/40').length;
    btn.querySelector('span').textContent = expandedRows.has(id) 
      ? `Nascondi dettagli flussi (${taskCount})`
      : `Mostra dettagli flussi (${taskCount})`;
    tasks.classList.toggle('hidden');
  }
}

async function updateProgress() {
  const data = await fetchDownloadData();
  renderActiveDownloads(data.active || []);
  renderScheduledDownloads(data.scheduled || []);
  renderHistory(data.history || []);
}

function escapeHtml(str) {
  if (typeof str !== 'string') return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

async function killDownload(id) {
  try {
    const response = await fetch(window.KILL_DOWNLOAD_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ download_id: id })
    });
    
    if (response.ok) {
      console.log(`Download ${id} cancellation requested`);
      updateProgress();
    } else {
      alert('Impossibile annullare il download.');
    }
  } catch (error) {
    console.error('Error killing download:', error);
  }
}

async function killAndClearQueue(id, seriesName = '') {
  let promptMsg = 'Sei sicuro di voler terminare questo download ORA ed eliminare TUTTI gli altri in attesa?';
  if (seriesName) {
    promptMsg = `Sei sicuro di voler terminare questo download ORA ed eliminare tutti quelli in attesa per la serie "${seriesName}"?`;
  }
  if (!confirm(promptMsg)) return;
  try {
    const payload = { download_id: id };
    if (seriesName) payload.series_name = seriesName;
    const response = await fetch(window.KILL_AND_CLEAR_QUEUE_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload)
    });
    
    if (response.ok) {
      console.log(`Download ${id} cancelled and queue cleared`);
      updateProgress();
    } else {
      alert('Impossibile svuotare la coda.');
    }
  } catch (error) {
    console.error('Error clearing queue:', error);
  }
}

async function killSeriesQueue(id, seriesName = '') {
  const msg = seriesName
    ? `Sei sicuro di voler annullare tutti i download in attesa per la serie "${seriesName}"?`
    : 'Sei sicuro di voler annullare tutti i download in attesa per questa serie?';
  if (!confirm(msg)) return;
  try {
    const payload = { download_id: id };
    if (seriesName) payload.series_name = seriesName;
    const response = await fetch(window.KILL_AND_CLEAR_QUEUE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (response.ok) {
      updateProgress();
    } else {
      alert('Impossibile annullare in massa i download programmati.');
    }
  } catch (error) {
    console.error('Error clearing series queue:', error);
  }
}

async function clearHistory() {
  if (!confirm('Sei sicuro di voler cancellare tutta la cronologia?')) return;
  try {
    const response = await fetch(window.CLEAR_HISTORY_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    if (response.ok) {
      updateProgress();
    } else {
      alert('Impossibile cancellare la cronologia.');
    }
  } catch (error) {
    console.error('Error clearing history:', error);
  }
}

export function init() {
  window.toggleTasks = toggleTasks;
  window.killDownload = killDownload;
  window.killAndClearQueue = killAndClearQueue;
  window.killSeriesQueue = killSeriesQueue;
  window.clearHistory = clearHistory;
  updateProgress();
  setInterval(updateProgress, UPDATE_INTERVAL);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
