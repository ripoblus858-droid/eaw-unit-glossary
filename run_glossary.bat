@echo off
python generate_glossary.py ^
	--dirs ^
	"C:\Program Files (x86)\Steam\steamapps\workshop\content\32470\1770851727\Data\Xml\Units\Space" ^
	"C:\Program Files (x86)\Steam\steamapps\workshop\content\32470\2794270450\Data\Xml\Units\Space" ^
	"C:\Program Files (x86)\Steam\steamapps\workshop\content\32470\3229239424\Data\Xml\Units\Space" ^
	--excluded-names-file excluded_names.txt ^
	--affiliation-overrides affiliation_overrides.txt ^
	--display-name-overrides display_name_overrides.txt ^
	--projectiles ^
	"C:\Program Files (x86)\Steam\steamapps\workshop\content\32470\3229239424\Data\Xml\PROJECTILES.XML" ^
	--output index.html ^
	--images-dir png_images ^
	--translations ^
	"C:\Program Files (x86)\Steam\steamapps\workshop\content\32470\3229239424\Data\Text\xml\TranslationManifest.xml" ^
	--image-size 100 ^
	--image-background-color "#1a2345" ^
	--in-game-images ingame_images.txt ^
	--in-game-images-dir screenshots ^
	--faction-logos faction_images.txt ^
	--faction-logos-dir faction_images ^
	--splash-config splash_text.txt ^
	--mod-icon "faction_images\fft_logo.png" ^
	--gameplay-image "faction_images\eaw_orbital.png" ^
	--unit-order glossary_unit_order.txt ^
	--prune-unused-images unused_png_images

pause
