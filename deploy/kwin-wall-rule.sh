#!/bin/sh
# One-time (as davidj, in the KDE session): KWin rules for the video wall. Idempotent; re-run after this file changes.
#  tv-wall          the tile windows (class ends in "-Wall", from chromium --profile-directory=Wall): borderless, and
#                   fullscreen forced off, so a site's fullscreen request fills only its tile.
# The overlay is a layer-shell surface now (src/tv/qml/Overlay.qml), not a window: the old tv-wall-overlay rule is removed.
set -eu
k() { kwriteconfig6 --file kwinrulesrc --group "$id" --key "$1" "$2"; }
add() {   # register rule group $id in General/rules
  rules=$(kreadconfig6 --file kwinrulesrc --group General --key rules)
  case ",$rules," in *",$id,"*) ;; *)
    rules="${rules:+$rules,}$id"
    kwriteconfig6 --file kwinrulesrc --group General --key rules "$rules"
    kwriteconfig6 --file kwinrulesrc --group General --key count "$(echo "$rules" | tr ',' '\n' | grep -c .)" ;;
  esac
}

id=tv-wall
k Description "TV OS video wall tiles"
k wmclass '.*-Wall$'
k wmclassmatch 3        # regex
k wmclasscomplete false
k fullscreen false
k fullscreenrule 2      # force
k noborder true
k noborderrule 2
add

# the chromium overlay window's rule (before the native overlay): drop it
rules=$(kreadconfig6 --file kwinrulesrc --group General --key rules)
case ",$rules," in *",tv-wall-overlay,"*)
  rules=$(echo "$rules" | tr ',' '\n' | grep -vx tv-wall-overlay | paste -sd, -)
  kwriteconfig6 --file kwinrulesrc --group General --key rules "$rules"
  kwriteconfig6 --file kwinrulesrc --group General --key count "$(echo "$rules" | tr ',' '\n' | grep -c .)" ;;
esac

qdbus6 org.kde.KWin /KWin reconfigure
echo "kwin rule tv-wall installed"
