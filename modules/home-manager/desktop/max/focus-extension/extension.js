import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const IFACE = `<node>
  <interface name="org.home_manager.MAXClipboardFocus">
    <method name="GetFocus">
      <arg type="u" direction="in" name="pid"/>
      <arg type="b" direction="out" name="focused"/>
      <arg type="u" direction="out" name="serial"/>
    </method>
  </interface>
</node>`;

export default class MaxClipboardFocus extends Extension {
    enable() {
        // The serial also invalidates transfers across a quick focus loss/regain.
        this._serial = 1;
        this._signals = [];
        const watch = (object, signal) => {
            this._signals.push([object, object.connect(signal, () => this._serial++)]);
        };
        watch(global.display, 'notify::focus-window');
        watch(Main.overview, 'showing');
        watch(Main.overview, 'hidden');
        watch(Main.sessionMode, 'updated');
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/home_manager/MAXClipboardFocus');
    }

    GetFocus(pid) {
        const window = global.display.focus_window;
        const focused = pid > 0 && Boolean(window) && window.get_pid() === pid &&
            !Main.overview.visible && !Main.sessionMode.isLocked && !Main.screenShield.locked;
        return [focused, this._serial];
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
        for (const [object, id] of this._signals ?? [])
            object.disconnect(id);
        this._signals = [];
    }
}
