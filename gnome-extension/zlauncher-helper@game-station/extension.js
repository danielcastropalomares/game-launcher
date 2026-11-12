import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const LAUNCHER = 'zlauncher';

export default class ZLauncherHelper extends Extension {
    enable() {
        this._timeouts = new Set();
        this._createdId = global.display.connect('window-created', (_d, win) => this._onWindow(win));
    }

    disable() {
        if (this._createdId)
            global.display.disconnect(this._createdId);
        this._createdId = 0;
        for (const id of this._timeouts)
            GLib.source_remove(id);
        this._timeouts.clear();
    }

    _isLauncher(win) {
        return (win.get_wm_class() ?? '') === LAUNCHER;
    }

    _launcherOpen() {
        return global.get_window_actors().some(a => this._isLauncher(a.meta_window));
    }

    _onWindow(win) {
        if (this._isLauncher(win) || !this._launcherOpen())
            return;
        // A game window: bring it to the front as soon as it is drawn, and
        // again a moment later in case it went fullscreen behind the launcher.
        let tries = 0;
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 250, () => {
            tries++;
            if (!win.get_compositor_private()) {   // window is gone
                this._timeouts.delete(id);
                return GLib.SOURCE_REMOVE;
            }
            if (win.get_window_type() === Meta.WindowType.NORMAL && !win.minimized &&
                global.display.focus_window !== win)
                Main.activateWindow(win);
            if (tries >= 8) {
                this._timeouts.delete(id);
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
        this._timeouts.add(id);
    }
}
