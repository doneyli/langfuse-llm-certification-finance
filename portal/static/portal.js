/* Certification Portal — Chart.js helpers */

const COLORS = {
    green: '#10b981',
    red: '#ef4444',
    blue: '#3b82f6',
    amber: '#f59e0b',
    navy: '#0a1628',
    steel: '#8899b0',
    cloud: '#e8edf3',
};

/**
 * Render a horizontal bar chart of evaluator scores.
 */
function renderBreakdownChart(canvasId, aggregates, threshold) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !aggregates) return;

    const names = Object.keys(aggregates).sort();
    const means = names.map(n => aggregates[n].mean);
    const colors = means.map(v => v >= threshold ? COLORS.green : COLORS.red);

    new Chart(canvas, {
        type: 'bar',
        data: {
            labels: names,
            datasets: [{
                label: 'Mean Score',
                data: means,
                backgroundColor: colors,
                borderRadius: 4,
                barThickness: 28,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    min: 0,
                    max: 1,
                    ticks: {
                        callback: v => (v * 100) + '%',
                        font: { family: "'JetBrains Mono', monospace", size: 11 },
                        color: COLORS.steel,
                    },
                    grid: { color: COLORS.cloud },
                },
                y: {
                    ticks: {
                        font: { family: "'DM Sans', sans-serif", size: 12, weight: 500 },
                        color: COLORS.navy,
                    },
                    grid: { display: false },
                },
            },
            plugins: {
                legend: { display: false },
                annotation: threshold ? {
                    annotations: {
                        threshold: {
                            type: 'line',
                            xMin: threshold,
                            xMax: threshold,
                            borderColor: COLORS.amber,
                            borderWidth: 2,
                            borderDash: [6, 4],
                            label: {
                                display: true,
                                content: 'Threshold',
                                position: 'start',
                            },
                        },
                    },
                } : {},
                tooltip: {
                    callbacks: {
                        label: ctx => (ctx.raw * 100).toFixed(1) + '%',
                    },
                },
            },
        },
    });
}

/**
 * Render a line chart of primary score over time.
 */
function renderHistoryChart(canvasId, runs) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !runs || !runs.length) return;

    // Filter to runs with scores, reverse for chronological order
    const scored = runs.filter(r => r.primary_score !== null).reverse();
    if (!scored.length) return;

    const labels = scored.map(r => {
        const d = r.timestamp.substring(0, 10);
        const model = r.model.split('-').slice(-2, -1)[0] || r.model;
        return d + ' ' + model;
    });
    const values = scored.map(r => r.primary_score);
    const threshold = scored[0].threshold;
    const colors = values.map(v => v >= threshold ? COLORS.green : COLORS.red);

    new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: scored[0].primary_name,
                    data: values,
                    borderColor: COLORS.blue,
                    backgroundColor: COLORS.blue + '20',
                    fill: true,
                    tension: 0.3,
                    pointBackgroundColor: colors,
                    pointBorderColor: colors,
                    pointRadius: 6,
                    pointHoverRadius: 8,
                },
                {
                    label: 'Threshold',
                    data: values.map(() => threshold),
                    borderColor: COLORS.amber,
                    borderWidth: 2,
                    borderDash: [6, 4],
                    pointRadius: 0,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    min: 0,
                    max: 1,
                    ticks: {
                        callback: v => (v * 100) + '%',
                        font: { family: "'JetBrains Mono', monospace", size: 11 },
                        color: COLORS.steel,
                    },
                    grid: { color: COLORS.cloud },
                },
                x: {
                    ticks: {
                        font: { size: 10 },
                        color: COLORS.steel,
                        maxRotation: 45,
                    },
                    grid: { display: false },
                },
            },
            plugins: {
                legend: {
                    labels: {
                        font: { family: "'DM Sans', sans-serif", size: 12 },
                        usePointStyle: true,
                        pointStyle: 'circle',
                    },
                },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            if (ctx.datasetIndex === 1) return 'Threshold: ' + (ctx.raw * 100) + '%';
                            return ctx.dataset.label + ': ' + (ctx.raw * 100).toFixed(1) + '%';
                        },
                    },
                },
            },
        },
    });
}
