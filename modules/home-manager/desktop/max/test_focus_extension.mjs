// Run with node modules/programs/max/test_focus_extension.mjs.
// Stub the shell API to exercise the extension's policy and signal lifecycle.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Signals {
    constructor() { this.handlers = new Map(); this.next = 1; }
    connect(signal, callback) {
        const id = this.next++;
        this.handlers.set(id, {signal, callback});
        return id;
    }
    disconnect(id) { assert.ok(this.handlers.delete(id)); }
    emit(signal) {
        for (const entry of this.handlers.values())
            if (entry.signal === signal) entry.callback();
    }
}
const display = new Signals();
const overview = new Signals();
overview.visible = false;
const sessionMode = new Signals();
sessionMode.isLocked = false;
const screenShield = {locked: false};
let exported = false;
const context = {
    Extension: class {},
    Gio: {
        DBus: {session: {}},
        DBusExportedObject: {wrapJSObject: () => ({
            export: (_bus, path) => {
                assert.equal(path, '/org/home_manager/MAXClipboardFocus');
                exported = true;
            },
            unexport: () => { exported = false; },
        })},
    },
    global: {display},
    Main: {overview, sessionMode, screenShield},
};
const source = fs.readFileSync(new URL('./focus-extension/extension.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '')
    .replace('export default class', 'class');
vm.runInNewContext(source + '\nthis.Policy = MaxClipboardFocus;', context);
const extension = new context.Policy();
extension.enable();
assert.ok(exported);
assert.equal(extension.GetFocus(123)[0], false); // No window.
display.focus_window = {get_pid: () => 123};
assert.equal(extension.GetFocus(123)[0], true);
assert.equal(extension.GetFocus(124)[0], false); // A matching title cannot grant access.
assert.equal(extension.GetFocus(0)[0], false);
const before = extension.GetFocus(123)[1];
display.emit('notify::focus-window');
display.emit('notify::focus-window');
assert.equal(extension.GetFocus(123)[1], before + 2); // Fast blur/refocus is not lost.
for (const [object, field] of [[overview, 'visible'], [sessionMode, 'isLocked'], [screenShield, 'locked']]) {
    object[field] = true;
    assert.equal(extension.GetFocus(123)[0], false);
    object[field] = false;
}
const serial = extension.GetFocus(123)[1];
overview.emit('showing');
overview.emit('hidden');
sessionMode.emit('updated');
assert.equal(extension.GetFocus(123)[1], serial + 3);
extension.disable();
assert.equal(exported, false);
for (const object of [display, overview, sessionMode])
    assert.equal(object.handlers.size, 0);
console.log('GNOME focus policy and lifecycle: OK');
