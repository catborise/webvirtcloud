// Live line charts of the host overview and the VM page (Chart.js 4): the
// last CHART_POINTS readings, a gap where a reading is missing, and the
// colours of the selected Bootstrap theme.
var CHART_POINTS = 10;

(function () {
    var style = getComputedStyle(document.body);
    Chart.defaults.color = style.getPropertyValue("--bs-body-color").trim();
    Chart.defaults.borderColor = style.getPropertyValue("--bs-border-color").trim();
})();

// labels: one per line. options: max (the y axis top), tooltip(context, text)
// to add to a point's tooltip text.
function lineChart(canvas, unit, labels, options) {
    var style = getComputedStyle(document.body);
    var colours = ["--bs-primary-rgb", "--bs-info-rgb"].map(function (name) {
        return style.getPropertyValue(name).trim();
    });
    options = options || {};
    return new Chart(canvas, {
        type: "line",
        data: {
            labels: [],
            datasets: labels.map(function (label, i) {
                return {
                    label: label,
                    data: [],
                    borderColor: "rgb(" + colours[i] + ")",
                    backgroundColor: "rgba(" + colours[i] + ", 0.3)",
                    fill: true,
                    pointRadius: 2,
                };
            }),
        },
        options: {
            animation: false,
            maintainAspectRatio: false,
            scales: {
                x: {ticks: {maxTicksLimit: CHART_POINTS, maxRotation: 0}},
                y: {min: 0, max: options.max, ticks: {callback: function (value) { return value + " " + unit; }}},
            },
            plugins: {
                legend: {display: labels.length > 1},
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            var text = context.dataset.label + ": " + context.formattedValue + " " + unit;
                            return options.tooltip ? options.tooltip(context, text) : text;
                        },
                    },
                },
            },
        },
    });
}

// values: one per line, null for a reading that is missing
function addPoint(chart, label, values) {
    chart.data.labels.push(label);
    chart.data.datasets.forEach(function (set, i) {
        set.data.push(values[i] == null ? null : values[i]);
    });
    if (chart.data.labels.length > CHART_POINTS) {
        chart.data.labels.shift();
        chart.data.datasets.forEach(function (set) {
            set.data.shift();
        });
    }
    chart.update();
}
