import json
import time
import urllib.request
from datetime import datetime

from flask import flash, jsonify, redirect, render_template, url_for

LATEST_URL = 'https://raw.githubusercontent.com/adamrfreeman644/raes-bits-and-bobbins-stock/main/VERSION'


def version_tuple(value):
    try:
        return tuple(int(x) for x in str(value).strip().lstrip('v').split('.'))
    except Exception:
        return (0,)


def latest_version():
    try:
        url = f"{LATEST_URL}?nocache={time.time_ns()}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'raes-stock-updater',
            'Cache-Control': 'no-cache, no-store, max-age=0',
            'Pragma': 'no-cache',
        })
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.read().decode('utf-8').strip()
    except Exception:
        return None


def read_text(path, default=''):
    try:
        return path.read_text(errors='replace').strip()
    except OSError:
        return default


def tail(path, limit=80):
    try:
        return '\n'.join(path.read_text(errors='replace').splitlines()[-limit:])
    except OSError:
        return ''


def friendly_status(raw):
    raw = (raw or '').strip().lower()
    if raw in ('running', 'starting'):
        return 'Installing', 'working'
    if raw == 'queued':
        return 'Waiting to start', 'working'
    if raw == 'complete':
        return 'Update finished', 'good'
    if raw.startswith('failed'):
        return 'Update needs attention', 'bad'
    if raw == 'idle':
        return 'Ready', 'good'
    return 'Ready', 'good'


def configure(app, updater_dir, current_version):
    def updates_view():
        latest = latest_version()
        raw_status = read_text(updater_dir / 'status', 'idle') or 'idle'
        status_label, status_kind = friendly_status(raw_status)
        update_available = bool(latest and version_tuple(latest) > version_tuple(current_version))
        return render_template(
            'updates.html',
            current_version=current_version,
            latest_version=latest,
            update_available=update_available,
            updater_status=raw_status,
            status_label=status_label,
            status_kind=status_kind,
            update_log=tail(updater_dir / 'update.log'),
            checked_at=datetime.now().strftime('%H:%M:%S'),
        )

    def install_view():
        latest = latest_version()
        if not latest:
            flash('I could not reach GitHub. Nothing has been changed. Try Check Again.', 'error')
            return redirect(url_for('updates'))
        if version_tuple(latest) <= version_tuple(current_version):
            flash('You already have the latest version.', 'info')
            return redirect(url_for('updates'))
        try:
            (updater_dir / 'status').write_text('queued')
            (updater_dir / 'update.request').write_text(datetime.now().isoformat(timespec='seconds'))
            flash(f'Update v{latest} has been queued. This page will refresh automatically.', 'success')
        except OSError as exc:
            flash(f'The update could not be started: {exc}', 'error')
        return redirect(url_for('updates'))

    def status_view():
        latest = latest_version()
        raw_status = read_text(updater_dir / 'status', 'idle') or 'idle'
        status_label, status_kind = friendly_status(raw_status)
        return jsonify({
            'current': current_version,
            'latest': latest,
            'raw_status': raw_status,
            'status_label': status_label,
            'status_kind': status_kind,
            'update_available': bool(latest and version_tuple(latest) > version_tuple(current_version)),
            'log': tail(updater_dir / 'update.log', 24),
        })

    app.view_functions['updates'] = updates_view
    app.view_functions['install_update'] = install_view
    if 'update_status_json' not in app.view_functions:
        app.add_url_rule('/updates/status.json', endpoint='update_status_json', view_func=status_view, methods=['GET'])
