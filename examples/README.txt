HopperDock — examples folder
============================

The shortcuts in the "Scripts" category on a fresh install point at the .bat
files in this folder. They're here so a new install has something that
actually runs, and so it's obvious where to put your own scripts.

Adding your own
---------------
1. Drop a .bat / .cmd / .ps1 / .py file in here (or anywhere else).
2. On the dock, click a category button to open its popout.
3. Click the pencil icon in the popout header to enter edit mode.
4. Click "+ Add Shortcut" and pick your file.

You can also drag a file straight onto the dock.

How shortcuts are launched
--------------------------
  .py           ->  pythonw <file>
  .ps1          ->  powershell -ExecutionPolicy Bypass -File <file>
  .bat / .cmd   ->  run directly
  anything else ->  handed to Windows (so .exe, .lnk, folders, and URLs
                    like steam://run/250820 or ms-settings:sound all work)

Where your settings live
------------------------
Your categories, shortcuts, layouts and preferences are stored per-user in:

  %USERPROFILE%\WindowDock\

    shortcuts.json   categories + their shortcuts
    layouts.json     saved window layouts
    settings.json    dock position, orientation, pinned apps, theme
    logs\            window_dock.log

Nothing in this folder is overwritten by the app, so edits here are safe.
Deleting %USERPROFILE%\WindowDock\shortcuts.json resets the categories back
to the starter set defined in the app.
