# -*- coding: utf-8 -*-
#
# This file is part of MyPaint.
# Copyright (C) 2007-2008 by Martin Renold <martinxyz@gmx.ch>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

"""
This is the main drawing window, containing menu actions.
Painting is done in tileddrawwidget.py.
"""

MYPAINT_VERSION="1.0.0+git"

import os, time, webbrowser
from gettext import gettext as _

import gtk, gobject
from gtk import gdk, keysyms

import colorselectionwindow, historypopup, stategroup, colorpicker, windowing, layout, toolbar
import dialogs
from lib import helpers
import stock
import dragfunc

import xml.etree.ElementTree as ET

# palette support
from lib.scratchpad_palette import GimpPalette, hatch_squiggle, squiggle, draw_palette

from overlays import LastPaintPosOverlay, ScaleOverlay



# TODO: put in a helper file?
def with_wait_cursor(func):
    """python decorator that adds a wait cursor around a function"""
    def wrapper(self, *args, **kwargs):
        toplevels = [t for t in gtk.window_list_toplevels()
                     if t.window is not None]
        for toplevel in toplevels:
            toplevel.window.set_cursor(gdk.Cursor(gdk.WATCH))
            toplevel.set_sensitive(False)
        self.app.doc.tdw.grab_add()
        try:
            func(self, *args, **kwargs)
            # gtk main loop may be called in here...
        finally:
            for toplevel in toplevels:
                toplevel.set_sensitive(True)
                # ... which is why we need this check:
                if toplevel.window is not None:
                    toplevel.window.set_cursor(None)
            self.app.doc.tdw.grab_remove()
    return wrapper

def button_press_cb_abstraction(drawwindow, win, event, doc):
    #print event.device, event.button
    ## Ignore accidentals
    # Single button-presses only, not 2ble/3ple
    if event.type != gdk.BUTTON_PRESS:
        # pass through the extra double-click event
        return False

    if event.button != 1:
        # check whether we are painting (accidental)
        if event.state & gdk.BUTTON1_MASK:
            # Do not allow dragging in the middle of
            # painting. This often happens by accident with wacom
            # tablet's stylus button.
            #
            # However we allow dragging if the user's pressure is
            # still below the click threshold.  This is because
            # some tablet PCs are not able to produce a
            # middle-mouse click without reporting pressure.
            # https://gna.org/bugs/index.php?15907
            return False

    # Line Mode event
    if event.button == 1:
        line_mode = drawwindow.app.linemode.line_mode
        # Dynamic Line events from toolbar settings
        if line_mode != "FreehandMode":
            drag_op = dragfunc.DynamicLineDragFunc(doc, drawwindow, mode=line_mode)
            doc.tdw.start_drag(drag_op)
            return True

    # Pick a suitable config option
    ctrl = event.state & gdk.CONTROL_MASK
    alt  = event.state & gdk.MOD1_MASK
    shift = event.state & gdk.SHIFT_MASK
    if shift:
        modifier_str = "_shift"
    elif alt or ctrl:
        modifier_str = "_ctrl"
    else:
        modifier_str = ""
    prefs_name = "input.button%d%s_action" % (event.button, modifier_str)
    action_name = drawwindow.app.preferences.get(prefs_name, "no_action")

    # No-ops
    if action_name == 'no_action':
        return False  # Pass event through to the tdw

    # Line Mode event triggered by preferenced modifier button
    if action_name == 'straight_line':
        drag_op = dragfunc.DynamicLineDragFunc(doc, drawwindow, mode='StraightMode')
        doc.tdw.start_drag(drag_op)
        return True
    if action_name == 'straight_line_sequence':
        drag_op = dragfunc.DynamicLineDragFunc(doc, drawwindow, mode='SequenceMode')
        doc.tdw.start_drag(drag_op)
        return True
    if action_name == 'ellipse':
        drag_op = dragfunc.DynamicLineDragFunc(doc, drawwindow, mode='EllipseMode')
        doc.tdw.start_drag(drag_op)
        return True

    # View control
    if action_name.endswith("_canvas"):
        drag_op = None
        if action_name == "pan_canvas":
            drag_op = dragfunc.PanViewDragFunc(doc)
        elif action_name == "zoom_canvas":
            drag_op = dragfunc.ZoomViewDragFunc(doc)
        elif action_name == "rotate_canvas":
            drag_op = dragfunc.RotateViewDragFunc(doc)
        if drag_op is not None:
            doc.tdw.start_drag(drag_op)
            return True
        return False

    # TODO: add frame manipulation here too

    if action_name == 'move_layer':
        drag_op = dragfunc.LayerMoveDragFunc(doc)
        doc.tdw.start_drag(drag_op)
        return True

    # Application menu
    if action_name == 'popup_menu':
        drawwindow.show_popupmenu(event=event)
        return True

    if action_name in drawwindow.popup_states:
        state = drawwindow.popup_states[action_name]
        state.activate(event)
        return True

    # Dispatch regular GTK events.
    action = drawwindow.app.find_action(action_name)
    if action is not None:
        action.activate()
        return True

def button_release_cb_abstraction(win, event, doc):
    #print event.device, event.button
    doc.tdw.stop_drag()
    return False

class Window (windowing.MainWindow, layout.MainWindow):

    MENUISHBAR_RADIO_MENUBAR = 1
    MENUISHBAR_RADIO_MAIN_TOOLBAR = 2
    MENUISHBAR_RADIO_BOTH_BARS = 3

    def __init__(self, app):
        windowing.MainWindow.__init__(self, app)
        self.app = app

        # Window handling
        self._updating_toggled_item = False
        self._show_subwindows = True
        self.is_fullscreen = False

        # Enable drag & drop
        self.drag_dest_set(gtk.DEST_DEFAULT_MOTION |
                            gtk.DEST_DEFAULT_HIGHLIGHT |
                            gtk.DEST_DEFAULT_DROP,
                            [("text/uri-list", 0, 1),
                             ("application/x-color", 0, 2)],
                            gtk.gdk.ACTION_DEFAULT|gtk.gdk.ACTION_COPY)

        # Connect events
        self.connect('delete-event', self.quit_cb)
        self.connect('key-press-event', self.key_press_event_cb_before)
        self.connect('key-release-event', self.key_release_event_cb_before)
        self.connect_after('key-press-event', self.key_press_event_cb_after)
        self.connect_after('key-release-event', self.key_release_event_cb_after)
        self.connect("drag-data-received", self.drag_data_received)
        self.connect("window-state-event", self.window_state_event_cb)

        self.update_overlays()

        self.app.filehandler.current_file_observers.append(self.update_title)

        self.init_actions()

        lm = app.layout_manager
        layout.MainWindow.__init__(self, lm)

        # Park the focus on the main tdw rather than on the toolbar. Default
        # activation doesn't really mean much for MyPaint's main window, so
        # it's safe to do this and it looks better.
        self.main_widget.set_can_default(True)
        self.main_widget.set_can_focus(True)
        self.main_widget.grab_focus()

        self.main_widget.connect("button-press-event", self.button_press_cb)
        self.main_widget.connect("button-release-event",self.button_release_cb)
        self.main_widget.connect("scroll-event", self.scroll_cb)

        kbm = self.app.kbm
        kbm.add_extra_key('Menu', 'ShowPopupMenu')
        kbm.add_extra_key('Tab', 'ToggleSubwindows')

        self.init_stategroups()

    #XXX: Compatability
    def get_doc(self):
        print "DeprecationWarning: Use app.doc instead"
        return self.app.doc
    def get_tdw(self):
        print "DeprecationWarning: Use app.doc.tdw instead"
        return self.app.doc.tdw
    tdw, doc = property(get_tdw), property(get_doc)

    def init_actions(self):
        actions = [
            # name, stock id, label, accelerator, tooltip, callback
            ('FileMenu',    None, _('File')),
            ('Quit',         gtk.STOCK_QUIT, _('Quit'), '<control>q', None, self.quit_cb),
            ('FrameToggle',  None, _('Toggle Document Frame'), None, None, self.toggle_frame_cb),

            ('EditMenu',        None, _('Edit')),

            ('ColorMenu',    None, _('Color')),
            ('ColorPickerPopup',    gtk.STOCK_COLOR_PICKER, _('Pick Color'), 'r', None, self.popup_cb),
            ('ColorHistoryPopup',  None, _('Color History'), 'x', None, self.popup_cb),
            ('ColorChangerCrossedBowlPopup', None, _('Color Changer (crossed bowl)'), 'v', None, self.popup_cb),
            ('ColorChangerWashPopup', None, _('Color Changer (washed)'), 'c', None, self.popup_cb),
            ('ColorRingPopup',  None, _('Color Ring'), None, None, self.popup_cb),
            ('ColorDetailsDialog', None, _("Color Details"), None, None, self.color_details_dialog_cb),

            ('ContextMenu',  None, _('Brushkeys')),
            ('ContextHelp',  gtk.STOCK_HELP, _('Help!'), None, None, self.show_infodialog_cb),

            ('LayerMenu',    None, _('Layers')),

            # Scratchpad menu items
            ('ScratchMenu',    None, _('Scratchpad')),
            ('ScratchNew',  gtk.STOCK_NEW, _('New Scratchpad'), '', None, self.new_scratchpad_cb),
            ('ScratchLoad',  gtk.STOCK_OPEN, _('Load Scratchpad...'), '', None, self.load_scratchpad_cb),
            ('ScratchSaveNow',  gtk.STOCK_SAVE, _('Save Scratchpad Now'), '', None, self.save_current_scratchpad_cb),
            ('ScratchSaveAs',  gtk.STOCK_SAVE_AS, _('Save Scratchpad As...'), '', None, self.save_as_scratchpad_cb),
            ('ScratchRevert',  gtk.STOCK_UNDO, _('Revert Scratchpad'), '', None, self.revert_current_scratchpad_cb),
            ('ScratchSaveAsDefault',  None, _('Save Scratchpad as Default'), None, None, self.save_scratchpad_as_default_cb),
            ('ScratchClearDefault',  None, _('Clear the Default Scratchpad'), None, None, self.clear_default_scratchpad_cb),
            ('ScratchPaletteOptions', None, _('Render a Palette')),
            ('ScratchLoadPalette',  None, _('Load Palette File...'), None, None, self.draw_palette_cb),
            ('ScratchDrawSatPalette',  None, _('Different Saturations of the current Color'), None, None, self.draw_sat_spectrum_cb),
            ('ScratchCopyBackground',  None, _('Copy Background to Scratchpad'), None, None, self.scratchpad_copy_background_cb),

            ('BrushMenu',    None, _('Brush')),
            ('BrushChooserPopup', stock.TOOL_BRUSH, _("Change Brush..."), 'b', None, self.brush_chooser_popup_cb),
            ('DownloadBrushPack', gtk.STOCK_OPEN, _('Download more brushes (in web browser)'), '', None, self.download_brush_pack_cb),
            ('ImportBrushPack', gtk.STOCK_OPEN, _('Import brush package...'), '', None, self.import_brush_pack_cb),

            ('HelpMenu',   None, _('Help')),
            ('Docu', gtk.STOCK_INFO, _('Where is the Documentation?'), None, None, self.show_infodialog_cb),
            ('ShortcutHelp',  gtk.STOCK_INFO, _('Change the Keyboard Shortcuts?'), None, None, self.show_infodialog_cb),
            ('About', gtk.STOCK_ABOUT, _('About MyPaint'), None, None, self.about_cb),

            ('DebugMenu',    None, _('Debug')),
            ('PrintMemoryLeak',  None, _('Print Memory Leak Info to Console (Slow!)'), None, None, self.print_memory_leak_cb),
            ('RunGarbageCollector',  None, _('Run Garbage Collector Now'), None, None, self.run_garbage_collector_cb),
            ('StartProfiling',  gtk.STOCK_EXECUTE, _('Start/Stop Python Profiling (cProfile)'), None, None, self.start_profiling_cb),
            ('GtkInputDialog',  None, _('GTK input device dialog'), None, None, self.gtk_input_dialog_cb),


            ('ViewMenu', None, _('View')),
            ('MenuishBarMenu', None, _('Toolbars')),
            ('FeedbackMenu', None, _('Feedback')),
            ('ShowPopupMenu',    None, _('Popup Menu'), 'Menu', None, self.popupmenu_show_cb),
            ('Fullscreen',   gtk.STOCK_FULLSCREEN, None, 'F11', None, self.fullscreen_cb),
            ('ViewHelp',  gtk.STOCK_HELP, _('Help'), None, None, self.show_infodialog_cb),
            ]
        ag = self.action_group = gtk.ActionGroup('WindowActions')
        self.app.add_action_group(ag)
        ag.add_actions(actions)
        self.update_fullscreen_action()

        # Toggle actions
        toggle_actions = [
            ('PreferencesWindow', gtk.STOCK_PREFERENCES,
                    _('Preferences'), None, None, self.toggle_window_cb),
            ('InputTestWindow',  None,
                    _('Test input devices'), None, None, self.toggle_window_cb),
            ('FrameWindow',  None,
                    _('Document Frame...'), None, None, self.toggle_window_cb),
            ('LayersWindow', stock.TOOL_LAYERS,
                    None, None, _("Toggle the Layers list"),
                    self.toggle_window_cb),
            ('BackgroundWindow', gtk.STOCK_PAGE_SETUP,
                    _('Background'), None, None, self.toggle_window_cb),
            ('BrushSelectionWindow', stock.TOOL_BRUSH,
                    None, None,
                    _("Edit and reorganise Brush Lists"),
                    self.toggle_window_cb),
            ('BrushSettingsWindow', gtk.STOCK_PROPERTIES,
                    _('Brush Settings Editor'), '<control>b',
                    _("Change Brush Settings in detail"),
                    self.toggle_window_cb),
            ('ColorSelectionWindow', stock.TOOL_COLOR_SELECTOR,
                    None, None, _("Toggle the Colour Triangle"),
                    self.toggle_window_cb),
            ('ColorSamplerWindow', stock.TOOL_COLOR_SAMPLER,
                    None, None, _("Toggle the advanced Colour Sampler"),
                    self.toggle_window_cb),
            ('ScratchWindow',  stock.TOOL_SCRATCHPAD, 
                    None, None, _('Toggle the scratchpad'),
                    self.toggle_window_cb),
            ]
        ag.add_toggle_actions(toggle_actions)

        # Reflect changes from other places (like tools' close buttons) into
        # the proxys' visible states.
        lm = self.app.layout_manager
        lm.tool_visibility_observers.append(self.update_toggled_item_visibility)
        lm.subwindow_visibility_observers.append(self.update_subwindow_visibility)

        # Initial toggle state
        for spec in toggle_actions:
            name = spec[0]
            action = ag.get_action(name)
            role = name[0].lower() + name[1:]
            visible = not lm.get_window_hidden_by_role(role)
            # The sidebar machinery won't be up yet, so reveal windows that
            # should be initially visible only in an idle handler
            gobject.idle_add(action.set_active, visible)

        # More toggle actions - ones which don't control windows.
        toggle_actions = [
            ('ToggleSubwindows', None, _('Subwindows'), 'Tab',
                    _("Show subwindows"), self.toggle_subwindows_cb,
                    self.get_show_subwindows()),
            ('ToggleScaleFeedback', None, _("Zoom Level"), None,
                    _("Reveal the scale % when zooming in or out"),
                    self.toggle_scale_feedback_cb,
                    self.app.preferences.get("ui.feedback.scale", False)),
            ('ToggleLastPosFeedback', None, _("Last Painting Position"), None,
                    _("Reveal the last painting pos when a stroke ends"),
                    self.toggle_last_pos_feedback_cb,
                    self.app.preferences.get("ui.feedback.last_pos", False)),
            ]
        ag.add_toggle_actions(toggle_actions)

        # Radio actions
        menuishbar_radio_actions = [
            ('MenuishBarRadioMenubar', None, _('Menubar only'), None,
                _("Show menu bar"),
                self.MENUISHBAR_RADIO_MENUBAR),
            ('MenuishBarRadioMainToolbar', None, _('Toolbar only'), None,
                _("Show toolbar"),
                self.MENUISHBAR_RADIO_MAIN_TOOLBAR),
            ('MenuishBarRadioMenubarAndMainToolbar', None, _('Both'), None,
                _("Show both the menu bar and the toolbar"),
                self.MENUISHBAR_RADIO_BOTH_BARS),
            ]
        menuishbar_state = 0
        if self.get_ui_part_enabled("menubar"):
            menuishbar_state += self.MENUISHBAR_RADIO_MENUBAR
        if self.get_ui_part_enabled("main_toolbar"):
            menuishbar_state += self.MENUISHBAR_RADIO_MAIN_TOOLBAR
        if menuishbar_state == 0:
            menuishbar_state = self.MENUISHBAR_RADIO_MAIN_TOOLBAR
        ag.add_radio_actions(menuishbar_radio_actions,
            menuishbar_state, self.on_menuishbar_radio_change)
        gobject.idle_add(lambda: self.update_ui_parts())

        # Keyboard handling
        for action in self.action_group.list_actions():
            self.app.kbm.takeover_action(action)

    def init_stategroups(self):
        sg = stategroup.StateGroup()
        p2s = sg.create_popup_state
        changer_crossed_bowl = p2s(colorselectionwindow.ColorChangerCrossedBowlPopup(self.app))
        changer_wash = p2s(colorselectionwindow.ColorChangerWashPopup(self.app))
        ring = p2s(colorselectionwindow.ColorRingPopup(self.app))
        hist = p2s(historypopup.HistoryPopup(self.app, self.app.doc.model))
        pick = self.colorpick_state = p2s(colorpicker.ColorPicker(self.app, self.app.doc.model))

        self.popup_states = {
            'ColorChangerCrossedBowlPopup': changer_crossed_bowl,
            'ColorChangerWashPopup': changer_wash,
            'ColorRingPopup': ring,
            'ColorHistoryPopup': hist,
            'ColorPickerPopup': pick,
            }

        # not sure how useful this is; we can't cycle at the moment
        changer_crossed_bowl.next_state = ring
        ring.next_state = changer_wash
        changer_wash.next_state = ring

        changer_wash.autoleave_timeout = None
        changer_crossed_bowl.autoleave_timeout = None
        ring.autoleave_timeout = None

        pick.max_key_hit_duration = 0.0
        pick.autoleave_timeout = None

        hist.autoleave_timeout = 0.600
        self.history_popup_state = hist

    def init_main_widget(self):  # override
        self.main_widget = self.app.doc.tdw

    def init_menubar(self):   # override
        # Load Menubar, duplicate into self.popupmenu
        menupath = os.path.join(self.app.datapath, 'gui/menu.xml')
        menubar_xml = open(menupath).read()
        self.app.ui_manager.add_ui_from_string(menubar_xml)
        self.popupmenu = self._clone_menu(menubar_xml, 'PopupMenu', self.app.doc.tdw)
        self.menubar = self.app.ui_manager.get_widget('/Menubar')

    def init_toolbar(self):
        self.toolbar_manager = toolbar.ToolbarManager(self)
        self.toolbar = self.toolbar_manager.toolbar1

    def _clone_menu(self, xml, name, owner=None):
        """
        Hopefully temporary hack for converting UIManager XML describing the
        main menubar into a rebindable popup menu. UIManager by itself doesn't
        let you do this, by design, but we need a bigger menu than the little
        things it allows you to build.
        """
        ui_elt = ET.fromstring(xml)
        rootmenu_elt = ui_elt.find("menubar")
        rootmenu_elt.attrib["name"] = name
        xml = ET.tostring(ui_elt)
        self.app.ui_manager.add_ui_from_string(xml)
        tmp_menubar = self.app.ui_manager.get_widget('/' + name)
        popupmenu = gtk.Menu()
        for item in tmp_menubar.get_children():
            tmp_menubar.remove(item)
            popupmenu.append(item)
        if owner is not None:
            popupmenu.attach_to_widget(owner, None)
        popupmenu.set_title("MyPaint")
        popupmenu.connect("selection-done", self.popupmenu_done_cb)
        popupmenu.connect("deactivate", self.popupmenu_done_cb)
        popupmenu.connect("cancel", self.popupmenu_done_cb)
        self.popupmenu_last_active = None
        return popupmenu


    def update_title(self, filename):
        if filename:
            self.set_title("MyPaint - %s" % os.path.basename(filename))
        else:
            self.set_title("MyPaint")

    # INPUT EVENT HANDLING
    def drag_data_received(self, widget, context, x, y, selection, info, t):
        if info == 1:
            if selection.data:
                uri = selection.data.split("\r\n")[0]
                fn = helpers.uri2filename(uri)
                if os.path.exists(fn):
                    if self.app.filehandler.confirm_destructive_action():
                        self.app.filehandler.open_file(fn)
        elif info == 2: # color
            color = [((ord(selection.data[v]) | (ord(selection.data[v+1]) << 8)) / 65535.0)  for v in range(0,8,2)]
            self.app.brush.set_color_rgb(color[:3])
            self.app.ch.push_color(self.app.brush.get_color_hsv())
            # Don't popup the color history for now, as I haven't managed to get it to cooperate.

    def print_memory_leak_cb(self, action):
        helpers.record_memory_leak_status(print_diff = True)

    def run_garbage_collector_cb(self, action):
        helpers.run_garbage_collector()

    def start_profiling_cb(self, action):
        if getattr(self, 'profiler_active', False):
            self.profiler_active = False
            return

        def doit():
            import cProfile
            profile = cProfile.Profile()

            self.profiler_active = True
            print '--- GUI Profiling starts ---'
            while self.profiler_active:
                profile.runcall(gtk.main_iteration, False)
                if not gtk.events_pending():
                    time.sleep(0.050) # ugly trick to remove "user does nothing" from profile
            print '--- GUI Profiling ends ---'

            profile.dump_stats('profile_fromgui.pstats')
            #print 'profile written to mypaint_profile.pstats'
            os.system('gprof2dot.py -f pstats profile_fromgui.pstats | dot -Tpng -o profile_fromgui.png && feh profile_fromgui.png &')

        gobject.idle_add(doit)

    def gtk_input_dialog_cb(self, action):
        d = gtk.InputDialog()
        d.show()

    def key_press_event_cb_before(self, win, event):
        key = event.keyval
        ctrl = event.state & gdk.CONTROL_MASK
        shift = event.state & gdk.SHIFT_MASK
        alt = event.state & gdk.MOD1_MASK
        #ANY_MODIFIER = gdk.SHIFT_MASK | gdk.MOD1_MASK | gdk.CONTROL_MASK
        #if event.state & ANY_MODIFIER:
        #    # allow user shortcuts with modifiers
        #    return False

        # This may need a stateful flag
        if self.app.scratchpad_doc.tdw.has_pointer:
            thisdoc = self.app.scratchpad_doc
            # Stop dragging on the main window
            self.app.doc.tdw.stop_drag()
        else:
            thisdoc = self.app.doc
            # Stop dragging on the other window
            self.app.scratchpad_doc.tdw.stop_drag()
        if key == keysyms.space:
            drag_op = None
            if (shift and ctrl) or alt:
                drag_op = dragfunc.MoveFrameDragFunc(thisdoc)
            elif shift:
                drag_op = dragfunc.RotateViewDragFunc(thisdoc)
            elif ctrl:
                drag_op = dragfunc.ZoomViewDragFunc(thisdoc)
            else:
                drag_op = dragfunc.PanViewDragFunc(thisdoc)
            assert drag_op is not None
            thisdoc.tdw.start_drag(drag_op)
        else: return False
        return True

    def key_release_event_cb_before(self, win, event):
        if self.app.scratchpad_doc.tdw.has_pointer:
            thisdoc = self.app.scratchpad_doc
        else:
            thisdoc = self.app.doc
        if event.keyval == keysyms.space:
            thisdoc.tdw.stop_drag()
            return True
        return False

    def key_press_event_cb_after(self, win, event):
        key = event.keyval
        if self.is_fullscreen and key == keysyms.Escape:
            self.fullscreen_cb()
        else:
            return False
        return True

    def key_release_event_cb_after(self, win, event):
        return False

    def button_press_cb(self, win, event):
        return button_press_cb_abstraction(self, win, event, self.app.doc)

    def button_release_cb(self, win, event):
        return button_release_cb_abstraction(win, event, self.app.doc)

    def scroll_cb(self, win, event):
        d = event.direction
        if d == gdk.SCROLL_UP:
            if event.state & gdk.SHIFT_MASK:
                self.app.doc.rotate('RotateLeft')
            else:
                self.app.doc.zoom('ZoomIn')
        elif d == gdk.SCROLL_DOWN:
            if event.state & gdk.SHIFT_MASK:
                self.app.doc.rotate('RotateRight')
            else:
                self.app.doc.zoom('ZoomOut')
        elif d == gdk.SCROLL_RIGHT:
            self.app.doc.rotate('RotateRight')
        elif d == gdk.SCROLL_LEFT:
            self.app.doc.rotate('RotateLeft')

    # WINDOW HANDLING
    def toggle_window_cb(self, action):
        if self._updating_toggled_item:
            return
        s = action.get_name()
        active = action.get_active()
        window_name = s[0].lower() + s[1:] # WindowName -> windowName
        # If it's a tool, get it to hide/show itself
        t = self.app.layout_manager.get_tool_by_role(window_name)
        if t is not None:
            t.set_hidden(not active)
            return
        # Otherwise, if it's a regular subwindow hide/show+present it.
        w = self.app.layout_manager.get_subwindow_by_role(window_name)
        if w is None:
            return
        onscreen = w.window is not None and w.window.is_visible()
        if active:
            if onscreen:
                return
            w.show_all()
            w.present()
        else:
            if not onscreen:
                return
            w.hide()

    def update_subwindow_visibility(self, window, active):
        # Responds to non-tool subwindows being hidden and shown
        role = window.get_role()
        self.update_toggled_item_visibility(role, active)

    def update_toggled_item_visibility(self, role, active, *a, **kw):
        # Responds to any item with a role being hidden or shown by
        # silently updating its ToggleAction to match.
        action_name = role[0].upper() + role[1:]
        action = self.action_group.get_action(action_name)
        if action is None:
            warn("Unable to find action %s" % action_name, RuntimeWarning, 1)
            return
        if action.get_active() != active:
            self._updating_toggled_item = True
            action.set_active(active)
            self._updating_toggled_item = False


    # Feedback and overlays
    # It's not intended that all categories of feedback will use overlays, but
    # they currently all do. This may change if/when we add a conventional
    # statusbar for textual types of feedback.

    def toggle_scale_feedback_cb(self, action):
        self.app.preferences['ui.feedback.scale'] = action.get_active()
        self.update_overlays()

    def toggle_last_pos_feedback_cb(self, action):
        self.app.preferences['ui.feedback.last_pos'] = action.get_active()
        self.update_overlays()

    def update_overlays(self):
        # Updates the list of overlays on the main doc's TDW to match the prefs
        doc = self.app.doc
        disp_overlays = [
            ('ui.feedback.scale', ScaleOverlay),
            ('ui.feedback.last_pos', LastPaintPosOverlay),
            ]
        for key, class_ in disp_overlays:
            current_instance = None
            for ov in doc.tdw.display_overlays:
                if isinstance(ov, class_):
                    current_instance = ov
            active = self.app.preferences.get(key, False)
            if active and not current_instance:
                doc.tdw.display_overlays.append(class_(doc))
            elif current_instance and not active:
                doc.tdw.display_overlays.remove(current_instance)


    def popup_cb(self, action):
        state = self.popup_states[action.get_name()]
        state.activate(action)


    def brush_chooser_popup_cb(self, action):
        # It may be even nicer to do this as a real popup state with
        # mouse-out to cancel. The Action is named accordingly. For now
        # though a modal dialog will do as an implementation.
        dialogs.change_current_brush_quick(self.app)

    def color_details_dialog_cb(self, action):
        dialogs.change_current_color_detailed(self.app)


    # User-toggleable UI pieces: things like toolbars, status bars, menu bars.
    # Saved between sessions.


    def get_ui_part_enabled(self, part_name):
        """Returns whether the named UI part is enabled in the prefs.
        """
        parts = self.app.preferences["ui.parts"]
        return bool(parts.get(part_name, False))


    def update_ui_parts(self, **updates):
        """Updates the UI part prefs, then hide/show widgets to match.

        Called without arguments, this updates the UI to match the
        boolean-valued hash ``ui.parts`` in the app preferences. With keyword
        arguments, the prefs are updated first, then changes are reflected in
        the set of visible widgets. Current known parts:

            :``main_toolbar``:
                The primary toolbar and its menu button.
            :``menubar``:
                A conventional menu bar.

        Currently the user cannot turn off both the main toolbar and the
        menubar: the toolbar will be forced on if an attempt is made.
        """
        new_state = self.app.preferences["ui.parts"].copy()
        new_state.update(updates)
        # Menu bar
        if new_state.get("menubar", False):
            self.menubar.show_all()
        else:
            self.menubar.hide()
            if not new_state.get("main_toolbar", False):
                new_state["main_toolbar"] = True
        # Toolbar
        if new_state.get("main_toolbar", False):
            self.toolbar.show_all()
        else:
            self.toolbar.hide()
        self.app.preferences["ui.parts"] = new_state
        self.update_menu_button()


    def update_menu_button(self):
        """Updates the menu button to match toolbar and menubar visibility.

        The menu button is visible when the menu bar is hidden. Since the user
        must have either a toolbar or a menu or both, this ensures that a menu
        is on-screen at all times in non-fullscreen mode.
        """
        toolbar_visible = self.toolbar.get_property("visible")
        menubar_visible = self.menubar.get_property("visible")
        if toolbar_visible and menubar_visible:
            self.toolbar_manager.menu_button.hide()
        else:
            self.toolbar_manager.menu_button.show_all()


    def on_menuishbar_radio_change(self, radioaction, current):
        """Respond to a change of the 'menu bar/toolbar' radio menu items.
        """
        value = radioaction.get_current_value()
        if value == self.MENUISHBAR_RADIO_MENUBAR:
            self.update_ui_parts(main_toolbar=False, menubar=True)
        elif value == self.MENUISHBAR_RADIO_MAIN_TOOLBAR:
            self.update_ui_parts(main_toolbar=True, menubar=False)
        else:
            self.update_ui_parts(main_toolbar=True, menubar=True)


    # Show Subwindows
    # Not saved between sessions, defaults to on.
    # Controlled via its ToggleAction, and entering or leaving fullscreen mode
    # according to the setting of ui.hide_in_fullscreen in prefs.

    def set_show_subwindows(self, show_subwindows):
        """Programatically set the Show Subwindows option.
        """
        action = self.action_group.get_action("ToggleSubwindows")
        currently_showing = action.get_active()
        if show_subwindows != currently_showing:
            action.set_active(show_subwindows)
        self._show_subwindows = self._show_subwindows

    def get_show_subwindows(self):
        return self._show_subwindows

    def toggle_subwindows_cb(self, action):
        active = action.get_active()
        lm = self.app.layout_manager
        if active:
            lm.toggle_user_tools(on=True)
        else:
            lm.toggle_user_tools(on=False)
        self._show_subwindows = active


    # Fullscreen mode
    # This implementation requires an ICCCM and EWMH-compliant window manager
    # which supports the _NET_WM_STATE_FULLSCREEN hint. There are several
    # available.

    def fullscreen_cb(self, *junk):
        if not self.is_fullscreen:
            self.fullscreen()
        else:
            self.unfullscreen()

    def window_state_event_cb(self, widget, event):
        # Respond to changes of the fullscreen state only
        if not event.changed_mask & gdk.WINDOW_STATE_FULLSCREEN:
            return
        lm = self.app.layout_manager
        self.is_fullscreen = event.new_window_state & gdk.WINDOW_STATE_FULLSCREEN
        if self.is_fullscreen:
            # Subwindow hiding 
            if self.app.preferences.get("ui.hide_subwindows_in_fullscreen", True):
                self.set_show_subwindows(False)
                self._restore_subwindows_on_unfullscreen = True
            if self.app.preferences.get("ui.hide_menubar_in_fullscreen", True):
                self.menubar.hide()
                self._restore_menubar_on_unfullscreen = True
            if self.app.preferences.get("ui.hide_toolbar_in_fullscreen", True):
                self.toolbar.hide()
                self._restore_toolbar_on_unfullscreen = True
            # fix for fullscreen problem on Windows, https://gna.org/bugs/?15175
            # on X11/Metacity it also helps a bit against flickering during the switch
            while gtk.events_pending():
                gtk.main_iteration()
        else:
            while gtk.events_pending():
                gtk.main_iteration()
            if getattr(self, "_restore_menubar_on_unfullscreen", False):
                if self.get_ui_part_enabled("menubar"):
                    self.menubar.show()
                del self._restore_menubar_on_unfullscreen
            if getattr(self, "_restore_toolbar_on_unfullscreen", False):
                if self.get_ui_part_enabled("main_toolbar"):
                    self.toolbar.show()
                del self._restore_toolbar_on_unfullscreen
            if getattr(self, "_restore_subwindows_on_unfullscreen", False):
                self.set_show_subwindows(True)
                del self._restore_subwindows_on_unfullscreen
        self.update_menu_button()
        self.update_fullscreen_action()

    def update_fullscreen_action(self):
        action = self.action_group.get_action("Fullscreen")
        if self.is_fullscreen:
            action.set_stock_id(gtk.STOCK_LEAVE_FULLSCREEN)
            action.set_tooltip(_("Leave Fullscreen Mode"))
            action.set_label(_("UnFullscreen"))
        else:
            action.set_stock_id(gtk.STOCK_FULLSCREEN)
            action.set_tooltip(_("Enter Fullscreen Mode"))
            action.set_label(_("Fullscreen"))

    def popupmenu_show_cb(self, action):
        self.show_popupmenu()

    def show_popupmenu(self, event=None):
        self.menubar.set_sensitive(False)   # excessive feedback?
        self.toolbar_manager.menu_button.set_sensitive(False)
        button = 1
        time = 0
        if event is not None:
            if event.type == gdk.BUTTON_PRESS:
                button = event.button
                time = event.time
        self.popupmenu.popup(None, None, None, button, time)
        if event is None:
            # We're responding to an Action, most probably the menu key.
            # Open out the last highlighted menu to speed key navigation up.
            if self.popupmenu_last_active is None:
                self.popupmenu.select_first(True) # one less keypress
            else:
                self.popupmenu.select_item(self.popupmenu_last_active)

    def popupmenu_done_cb(self, *a, **kw):
        # Not sure if we need to bother with this level of feedback,
        # but it actually looks quite nice to see one menu taking over
        # the other. Makes it clear that the popups are the same thing as
        # the full menu, maybe.
        self.menubar.set_sensitive(True)
        self.toolbar_manager.menu_button.set_sensitive(True)
        self.popupmenu_last_active = self.popupmenu.get_active()

    # BEGIN -- Scratchpad menu options
    def save_scratchpad_as_default_cb(self, action):
        self.app.filehandler.save_scratchpad(self.app.filehandler.get_scratchpad_default(), export = True)

    def clear_default_scratchpad_cb(self, action):
        self.app.filehandler.delete_default_scratchpad()

    # Unneeded since 'Save blank canvas' bug has been addressed.
    #def clear_autosave_scratchpad_cb(self, action):
    #    self.app.filehandler.delete_autosave_scratchpad()

    def new_scratchpad_cb(self, action):
        if os.path.isfile(self.app.filehandler.get_scratchpad_default()):
            self.app.filehandler.open_scratchpad(self.app.filehandler.get_scratchpad_default())
        else:
            self.app.scratchpad_doc.model.clear()
            # With no default - adopt the currently chosen background
            bg = self.app.doc.model.background
            if self.app.scratchpad_doc:
                self.app.scratchpad_doc.model.set_background(bg)

        self.app.scratchpad_filename = self.app.preferences['scratchpad.last_opened'] = self.app.filehandler.get_scratchpad_autosave()

    def load_scratchpad_cb(self, action):
        if self.app.scratchpad_filename:
            self.save_current_scratchpad_cb(action)
            current_pad = self.app.scratchpad_filename
        else:
            current_pad = self.app.filehandler.get_scratchpad_autosave()
        self.app.filehandler.open_scratchpad_dialog()
        # Check to see if a file has been opened outside of the scratchpad directory
        if not os.path.abspath(self.app.scratchpad_filename).startswith(os.path.abspath(self.app.filehandler.get_scratchpad_prefix())):
            # file is NOT within the scratchpad directory - load copy as current scratchpad
            self.app.scratchpad_filename = self.app.preferences['scratchpad.last_opened'] = current_pad

    def save_as_scratchpad_cb(self, action):
        self.app.filehandler.save_scratchpad_as_dialog()

    def revert_current_scratchpad_cb(self, action):
        if os.path.isfile(self.app.scratchpad_filename):
            self.app.filehandler.open_scratchpad(self.app.scratchpad_filename)
            print "Reverted to %s" % self.app.scratchpad_filename
        else:
            print "No file to revert to yet."

    def save_current_scratchpad_cb(self, action):
        self.app.filehandler.save_scratchpad(self.app.scratchpad_filename)

    def scratchpad_copy_background_cb(self, action):
        bg = self.app.doc.model.background
        if self.app.scratchpad_doc:
            self.app.scratchpad_doc.model.set_background(bg)

    def draw_palette_cb(self, action):
        # test functionality:
        file_filters = [
        (_("Gimp Palette Format"), ("*.gpl",)),
        (_("All Files"), ("*.*",)),
        ]
        gimp_path = os.path.join(self.app.filehandler.get_gimp_prefix(), "palettes")
        dialog = self.app.filehandler.get_open_dialog(start_in_folder=gimp_path,
                                                  file_filters = file_filters)
        try:
            if dialog.run() == gtk.RESPONSE_OK:
                dialog.hide()
                filename = dialog.get_filename().decode('utf-8')
                if filename:
                    #filename = "/home/ben/.gimp-2.6/palettes/Nature_Grass.gpl" # TEMP HACK TO TEST
                    g = GimpPalette(filename)
                    grid_size = 30.0
                    column_limit = 7
                    # IGNORE Gimp Palette 'columns'?
                    if g.columns != 0:
                        column_limit = g.columns   # use the value for columns in the palette
                    draw_palette(self.app, g, self.app.scratchpad_doc, columns=column_limit, grid_size=grid_size, swatch_method=hatch_squiggle, scale = 25.0)
        finally:
            dialog.destroy()

    def draw_sat_spectrum_cb(self, action):
        g = GimpPalette()
        hsv = self.app.brush.get_color_hsv()
        g.append_sat_spectrum(hsv)
        grid_size = 30.0
        off_x = off_y = grid_size / 2.0
        column_limit = 8
        draw_palette(self.app, g, self.app.scratchpad_doc, columns=column_limit, grid_size=grid_size)

    # END -- Scratchpad menu options

    def quit_cb(self, *junk):
        self.app.doc.model.split_stroke()
        self.app.save_gui_config() # FIXME: should do this periodically, not only on quit

        if not self.app.filehandler.confirm_destructive_action(title=_('Quit'), question=_('Really Quit?')):
            return True

        gtk.main_quit()
        return False

    def toggle_frame_cb(self, action):
        enabled = self.app.doc.model.frame_enabled
        self.app.doc.model.set_frame_enabled(not enabled)

    def download_brush_pack_cb(self, *junk):
        url = 'http://wiki.mypaint.info/index.php?title=Brush_Packages/redirect_mypaint_1.0_gui'
        print 'URL:', url
        webbrowser.open(url)

    def import_brush_pack_cb(self, *junk):
        format_id, filename = dialogs.open_dialog(_("Import brush package..."), self,
                                 [(_("MyPaint brush package (*.zip)"), "*.zip")])
        if filename:
            self.app.brushmanager.import_brushpack(filename,  self)

    # INFORMATION
    # TODO: Move into dialogs.py?
    def about_cb(self, action):
        d = gtk.AboutDialog()
        d.set_transient_for(self)
        d.set_program_name("MyPaint")
        d.set_version(MYPAINT_VERSION)
        d.set_copyright(_("Copyright (C) 2005-2012\nMartin Renold and the MyPaint Development Team"))
        d.set_website("http://mypaint.info/")
        d.set_logo(self.app.pixmaps.mypaint_logo)
        d.set_license(
            _(u"This program is free software; you can redistribute it and/or modify "
              u"it under the terms of the GNU General Public License as published by "
              u"the Free Software Foundation; either version 2 of the License, or "
              u"(at your option) any later version.\n"
              u"\n"
              u"This program is distributed in the hope that it will be useful, "
              u"but WITHOUT ANY WARRANTY. See the COPYING file for more details.")
            )
        d.set_wrap_license(True)
        d.set_authors([
            # (in order of appearance)
            u"Martin Renold (%s)" % _('programming'),
            u"Yves Combe (%s)" % _('portability'),
            u"Popolon (%s)" % _('programming'),
            u"Clement Skau (%s)" % _('programming'),
            u"Jon Nordby (%s)" % _('programming'),
            u"Álinson Santos (%s)" % _('programming'),
            u"Tumagonx (%s)" % _('portability'),
            u"Ilya Portnov (%s)" % _('programming'),
            u"Jonas Wagner (%s)" % _('programming'),
            u"Luka Čehovin (%s)" % _('programming'),
            u"Andrew Chadwick (%s)" % _('programming'),
            u"Till Hartmann (%s)" % _('programming'),
            u'David Grundberg (%s)' % _('programming'),
            u"Krzysztof Pasek (%s)" % _('programming'),
            u"Ben O'Steen (%s)" % _('programming'),
            u"Ferry Jérémie (%s)" % _('programming'),
            u"しげっち 'sigetch' (%s)" % _('programming'),
            u"Richard Jones (%s)" % _('programming'),
            u"David Gowers (%s)" % _('programming'),
            ])
        d.set_artists([
            u"Artis Rozentāls (%s)" % _('brushes'),
            u"Popolon (%s)" % _('brushes'),
            u"Marcelo 'Tanda' Cerviño (%s)" % _('patterns, brushes'),
            u"David Revoy (%s)" % _('brushes, tool icons'),
            u"Ramón Miranda (%s)" % _('brushes, patterns'),
            u"Enrico Guarnieri 'Ico_dY' (%s)" % _('brushes'),
            u'Sebastian Kraft (%s)' % _('desktop icon'),
            u"Nicola Lunghi (%s)" % _('patterns'),
            u"Toni Kasurinen (%s)" % _('brushes'),
            u"Сан Саныч 'MrMamurk' (%s)" % _('patterns'),
            u"Andrew Chadwick (%s)" % _('tool icons'),
            u"Ben O'Steen (%s)" % _('tool icons'),
            ])
        # list all translators, not only those of the current language
        d.set_translator_credits(
            u'Ilya Portnov (ru)\n'
            u'Popolon (fr, zh_CN, ja)\n'
            u'Jon Nordby (nb)\n'
            u'Griatch (sv)\n'
            u'Tobias Jakobs (de)\n'
            u'Martin Tabačan (cs)\n'
            u'Tumagonx (id)\n'
            u'Manuel Quiñones (es)\n'
            u'Gergely Aradszki (hu)\n'
            u'Lamberto Tedaldi (it)\n'
            u'Dong-Jun Wu (zh_TW)\n'
            u'Luka Čehovin (sl)\n'
            u'Geuntak Jeong (ko)\n'
            u'Łukasz Lubojański (pl)\n'
            u'Daniel Korostil (uk)\n'
            u'Julian Aloofi (de)\n'
            u'Tor Egil Hoftun Kvæstad (nn_NO)\n'
            u'João S. O. Bueno (pt_BR)\n'
            u'David Grundberg (sv)\n'
            u'Elliott Sales de Andrade (en_CA)\n'
            u'Ruei-Yuan Lu (zh_TW)\n'
            u'Cheng-Chia Tseng (zh_TW)\n'
            )

        d.run()
        d.destroy()

    def show_infodialog_cb(self, action):
        text = {
        'ShortcutHelp':
                _("Move your mouse over a menu entry, then press the key to assign."),
        'ViewHelp':
                _("You can also drag the canvas with the mouse while holding the middle "
                "mouse button or spacebar. Or with the arrow keys."
                "\n\n"
                "In contrast to earlier versions, scrolling and zooming are harmless now and "
                "will not make you run out of memory. But you still require a lot of memory "
                "if you paint all over while fully zoomed out."),
        'ContextHelp':
                _("Brushkeys are used to quickly save/restore brush settings "
                 "using keyboard shortcuts. You can paint with one hand and "
                 "change brushes with the other without interruption."
                 "\n\n"
                 "There are 10 memory slots to hold brush settings.\n"
                 "They are anonymous brushes, which are not visible in the "
                 "brush selector list. But they are remembered even if you "
                 "quit."),
        'Docu':
                _("There is a tutorial available on the MyPaint homepage. It "
                 "explains some features which are hard to discover yourself."
                 "\n\n"
                 "Comments about the brush settings (opaque, hardness, etc.) and "
                 "inputs (pressure, speed, etc.) are available as tooltips. "
                 "Put your mouse over a label to see them. "
                 "\n"),
        }
        self.app.message_dialog(text[action.get_name()])


