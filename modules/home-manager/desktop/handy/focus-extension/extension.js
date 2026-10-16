import Gio from 'gi://Gio';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const IFACE = `<node>
  <interface name="org.home_manager.HandyFocus">
    <method name="IsKitty">
      <arg type="b" direction="out" name="focused"/>
    </method>
  </interface>
</node>`;

export default class HandyFocus extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/home_manager/HandyFocus');
    }

    IsKitty() {
        const window = global.display.focus_window;
        if (!window || Main.overview.visible || Main.sessionMode.isLocked || Main.screenShield.locked)
            return false;
        // Kitty's Wayland app ID is exposed as wm_class by Mutter. The
        // instance also covers a custom --class with the default --name.
        return [window.get_wm_class(), window.get_wm_class_instance()]
            .some(value => typeof value === 'string' && value.toLowerCase() === 'kitty');
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
    }
}
