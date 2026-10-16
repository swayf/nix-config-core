// Run with node modules/programs/handy/test-focus-extension.mjs.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

let exported = false;
const context = {
    Extension: class {},
    Gio: {
        DBus: {session: {}},
        DBusExportedObject: {wrapJSObject: (iface, object) => {
            assert.match(iface, /name="org.home_manager.HandyFocus"/);
            assert.equal(typeof object.IsKitty, 'function');
            return {
                export: (_bus, path) => {
                    assert.equal(path, '/org/home_manager/HandyFocus');
                    exported = true;
                },
                unexport: () => { exported = false; },
            };
        }},
    },
    global: {display: {focus_window: null}},
    Main: {overview: {visible: false}, sessionMode: {isLocked: false}, screenShield: {locked: false}},
};
const source = fs.readFileSync(new URL('./focus-extension/extension.js', import.meta.url), 'utf8')
    .replace(/^import .*;\n/gm, '')
    .replace('export default class', 'class');
vm.runInNewContext(source + '\nthis.Policy = HandyFocus;', context);
const extension = new context.Policy();
extension.enable();
assert.ok(exported);
assert.equal(extension.IsKitty(), false);
for (const [wmClass, instance, expected] of [
    ['kitty', 'kitty', true], ['Kitty', null, true], ['custom', 'kitty', true],
    ['org.gnome.Nautilus', 'nautilus', false], ['my-kitty-app', '', false], [null, null, false],
]) {
    context.global.display.focus_window = {
        get_wm_class: () => wmClass, get_wm_class_instance: () => instance,
    };
    assert.equal(extension.IsKitty(), expected);
}
context.global.display.focus_window = {get_wm_class: () => 'kitty', get_wm_class_instance: () => 'kitty'};
for (const [object, field] of [
    [context.Main.overview, 'visible'], [context.Main.sessionMode, 'isLocked'], [context.Main.screenShield, 'locked'],
]) {
    object[field] = true;
    assert.equal(extension.IsKitty(), false);
    object[field] = false;
    assert.equal(extension.IsKitty(), true);
}
extension.disable();
assert.equal(exported, false);
extension.disable();
console.log('Handy GNOME focus policy and lifecycle: OK');
