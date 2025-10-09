// static/js/dashboard.js
const ws = new WebSocket(`ws://${window.location.host}/ws/dashboard`);
const machinesContainer = document.getElementById("machines-container");
const machineCharts = {};
const MAX_PLOT = 200;
const FFT_BINS = 128;

ws.onopen = () => console.log("Dashboard WS open");
ws.onmessage = (ev) => {
  try {
    const data = JSON.parse(ev.data);
    for (let id in data) {
      if (!machineCharts[id]) addMachinePanel(id);
      updateMachinePanel(id, data[id]);
    }
  } catch (e) {
    console.error("Failed to parse dashboard message", e);
  }
};
ws.onclose = () => console.log("Dashboard WS closed");

function addMachinePanel(id) {
  const container = document.createElement("div");
  container.className = "machine-panel";
  container.id = `machine-${id}`;

  container.innerHTML = `
    <div class="panel-head">
      <div class="title"><span class="status-indicator" id="status-${id}"></span><strong>${id}</strong></div>
      <div class="controls">
        <button onclick="sendCommand('${id}', 'stop')">Stop</button>
        <button onclick="sendCommand('${id}', 'restart')">Restart</button>
      </div>
    </div>
    <div class="canvas-wrap"><canvas id="vib-${id}"></canvas></div>
    <div class="canvas-wrap" style="height:100px;"><canvas id="fft-${id}"></canvas></div>
    <div class="small">Showing latest ${MAX_PLOT} smoothed samples · FFT: first ${FFT_BINS} bins</div>
  `;
  machinesContainer.appendChild(container);

  const ctxV = container.querySelector(`#vib-${id}`).getContext('2d');
  const ctxF = container.querySelector(`#fft-${id}`).getContext('2d');

  machineCharts[id] = {
    vibration: new Chart(ctxV, {
      type: 'line',
      data: {
        labels: Array(MAX_PLOT).fill(''),
        datasets: [
          { label: 'X', data: [], borderColor: '#fb7185', pointRadius: 0, tension: 0.35, borderWidth: 2 },
          { label: 'Y', data: [], borderColor: '#34d399', pointRadius: 0, tension: 0.35, borderWidth: 2 },
          { label: 'Z', data: [], borderColor: '#60a5fa', pointRadius: 0, tension: 0.35, borderWidth: 2 }
        ]
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#cfe7ff' } }, decimation: { enabled: true, algorithm: 'lttb', samples: 100 } },
        scales: { x: { display: false }, y: { ticks: { color: '#9fbce8' } } }
      }
    }),
    fft: new Chart(ctxF, {
      type: 'line',
      data: { labels: [], datasets: [{ label: 'FFT Magnitude', data: [], borderColor: '#f59e0b', pointRadius: 0, tension:0.2, borderWidth:1 }] },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { x: { display: false }, y: { ticks: { color: '#9fbce8' } } }
      }
    })
  };
}

function updateMachinePanel(id, machine) {
  const color = machine.status === 'active' ? '#10b981' : machine.status === 'warning' ? '#f59e0b' : '#ef4444';
  const statusEl = document.getElementById(`status-${id}`);
  if (statusEl) statusEl.style.backgroundColor = color;

  const charts = machineCharts[id];
  if (!charts) return;

  const xs = (machine.x || []).slice(-MAX_PLOT);
  const ys = (machine.y || []).slice(-MAX_PLOT);
  const zs = (machine.z || []).slice(-MAX_PLOT);

  charts.vibration.data.datasets[0].data = xs;
  charts.vibration.data.datasets[1].data = ys;
  charts.vibration.data.datasets[2].data = zs;
  charts.vibration.update('none');

  const fft = (machine.fft_magnitude || []).slice(0, FFT_BINS);
  charts.fft.data.labels = fft.map((_, i) => i);
  charts.fft.data.datasets[0].data = fft;
  charts.fft.update('none');
}

function sendCommand(id, command) {
  fetch('/command', { method:'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({ id, command })});
}
