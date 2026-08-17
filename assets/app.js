// Страница читает только свой data.json, который собирает GitHub Action.
// Никаких обращений к чужим хостам: и быстрее, и нечего блокировать.

(function () {
    'use strict';

    var MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
    var WEEKDAYS = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
    var DURATION_LABELS = { 30: '30 мин', 60: '1 час', 90: '1,5 часа' };

    var court = document.getElementById('court');
    var durations = document.getElementById('durations');
    var stamp = document.getElementById('stamp');
    var notice = document.getElementById('notice');
    var legend = document.getElementById('legend');

    var data = null;
    var selected = null;

    fetch('data.json?t=' + Math.floor(Date.now() / 60000))
        .then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        })
        .then(start)
        .catch(function () {
            court.innerHTML = '<p class="failed">Расписание не загрузилось. ' +
                'Обновите страницу или запишитесь <a href="https://tennisryb.ru/">через сайт центра</a>.</p>';
        });

    function start(loaded) {
        data = loaded;
        selected = pickDuration();

        renderSwitcher();
        renderStamp();
        renderLegend();
        renderGrid();
    }

    function pickDuration() {
        var asked = parseInt(new URLSearchParams(location.search).get('d'), 10);
        return data.durations.indexOf(asked) !== -1 ? asked : data.default_duration;
    }

    // Время центра, а не браузера: посетитель может смотреть из другого пояса,
    // а «прошедшие» слоты считаются по Москве.
    function localNow() {
        return new Date(new Date().toLocaleString('en-US', { timeZone: 'Europe/Moscow' }));
    }

    function minutes(clock) {
        var parts = clock.split(':');
        return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
    }

    function clock(total) {
        return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
    }

    function axis(duration) {
        var out = [];
        var limit = minutes(data.grid_to) - duration;
        for (var m = minutes(data.grid_from); m <= limit; m += data.slot_step) out.push(clock(m));
        return out;
    }

    function tariff(duration, dayOff, time) {
        var windows = (data.tariffs[String(duration)] || {})[dayOff ? 'weekend' : 'weekday'] || [];
        var at = minutes(time);
        for (var i = 0; i < windows.length; i++) {
            if (at >= minutes(windows[i].from) && at < minutes(windows[i].to)) return windows[i];
        }
        return null;
    }

    function price(entry) {
        if (!entry) return 'цену уточните при записи';
        return String(entry.price).replace('.', ',') + ' ₽';
    }

    function masterOf(number) {
        for (var i = 0; i < data.courts.length; i++) {
            if (data.courts[i].n === number) return data.courts[i].master_id;
        }
        return null;
    }

    function bookingUrl(number, entry) {
        var url = data.company.booking_base + '?st_m=1&sm_m=' + masterOf(number);
        return entry ? url + '&ss_s=' + entry.service_id : url;
    }

    function renderSwitcher() {
        durations.innerHTML = '';
        data.durations.forEach(function (duration) {
            var button = document.createElement('button');
            button.type = 'button';
            button.textContent = DURATION_LABELS[duration] || duration + ' мин';
            button.setAttribute('aria-pressed', String(duration === selected));
            button.addEventListener('click', function () {
                selected = duration;
                history.replaceState(null, '', '?d=' + duration);
                renderSwitcher();
                renderGrid();
            });
            durations.appendChild(button);
        });
    }

    function renderStamp() {
        var moment = new Date(data.generated_at);
        var age = (Date.now() - moment.getTime()) / 60000;

        stamp.innerHTML = 'обновлено <b>' + moment.toLocaleTimeString('ru-RU', {
            hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow',
        }) + '</b>';

        if (age > 90) {
            notice.hidden = false;
            notice.textContent = 'Расписание давно не обновлялось — данные от ' +
                moment.toLocaleString('ru-RU', {
                    day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
                    timeZone: 'Europe/Moscow',
                }) + '. Свободное время могло измениться.';
        }
    }

    function renderLegend() {
        legend.innerHTML = '';
        data.courts.forEach(function (item) {
            var wrap = document.createElement('span');
            wrap.className = 'legend-item';
            wrap.innerHTML = '<span class="chip c' + item.n + '">' + item.n + '</span> хард №' + item.n;
            legend.appendChild(wrap);
        });
    }

    function renderGrid() {
        var now = localNow();
        var today = now.getFullYear() + '-' +
            String(now.getMonth() + 1).padStart(2, '0') + '-' +
            String(now.getDate()).padStart(2, '0');
        var nowMinutes = now.getHours() * 60 + now.getMinutes();
        var grid = data.grids[String(selected)] || {};

        var table = document.createElement('table');

        var head = document.createElement('thead');
        var headRow = document.createElement('tr');
        headRow.appendChild(cell('th', 'gutter', ''));

        data.days.forEach(function (day, index) {
            var date = new Date(day + 'T00:00:00');
            var th = document.createElement('th');
            th.style.setProperty('--col', String(index));
            th.className = (data.day_off[day] ? 'off ' : '') + (day === today ? 'today' : '');
            th.innerHTML = '<span class="dnum">' + date.getDate() + '</span>' +
                '<span class="dmon">' + MONTHS[date.getMonth()] + ' ' + WEEKDAYS[date.getDay()] + '</span>';
            headRow.appendChild(th);
        });

        head.appendChild(headRow);
        table.appendChild(head);

        var body = document.createElement('tbody');

        axis(selected).forEach(function (time) {
            var row = document.createElement('tr');
            if (time.slice(3) === '00') row.className = 'sharp';
            row.appendChild(cell('th', 'gutter', time));

            data.days.forEach(function (day) {
                var td = document.createElement('td');
                var off = data.day_off[day];
                var past = day < today || (day === today && minutes(time) < nowMinutes);
                var numbers = (grid[day] || {})[time] || [];

                td.className = (off ? 'off ' : '') + (past ? 'past' : '');

                if (past || !numbers.length) {
                    td.innerHTML = '<span class="gap" aria-hidden="true">·</span>';
                } else {
                    var entry = tariff(selected, off, time);
                    var date = new Date(day + 'T00:00:00');
                    numbers.forEach(function (number) {
                        var link = document.createElement('a');
                        link.className = 'chip c' + number;
                        link.href = bookingUrl(number, entry);
                        link.target = '_blank';
                        link.rel = 'noopener';
                        link.textContent = number;
                        link.title = 'Корт №' + number + ', ' + date.getDate() + ' ' +
                            MONTHS[date.getMonth()] + ' в ' + time + ', ' +
                            (DURATION_LABELS[selected] || selected + ' мин') + ' — ' + price(entry);
                        td.appendChild(link);
                    });
                }

                row.appendChild(td);
            });

            body.appendChild(row);
        });

        table.appendChild(body);
        court.innerHTML = '';
        court.appendChild(table);
    }

    function cell(tag, className, text) {
        var node = document.createElement(tag);
        node.className = className;
        node.textContent = text;
        return node;
    }
})();
