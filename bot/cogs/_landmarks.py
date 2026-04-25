"""Landmark dataset for the geography game's Landmarks mode.

Each entry maps a country to a list of (landmark_name, image_url) tuples.
Image URLs use Wikimedia Commons direct format (public domain / CC).

At pool-build time, direct URLs are converted to ``thumb.php`` thumbnails
(800 px wide, typically 50–200 KB).  The originals can be multi-MB (some
exceed 60 MB), which causes Discord's embed proxy to time out — that is
the root cause of "only the first landmark loads."
"""

LANDMARKS: dict[str, list[tuple[str, str]]] = {
    # ── Europe (~20) ────────────────────────────────────────────────────────
    "France": [
        ("Eiffel Tower", "https://upload.wikimedia.org/wikipedia/commons/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg"),
        ("Mont Saint-Michel", "https://upload.wikimedia.org/wikipedia/commons/4/40/Mont_Saint-Michel_3.jpg"),
    ],
    "Italy": [
        ("Colosseum", "https://upload.wikimedia.org/wikipedia/commons/d/de/Colosseo_2020.jpg"),
        ("Leaning Tower of Pisa", "https://upload.wikimedia.org/wikipedia/commons/6/66/The_Leaning_Tower_of_Pisa_SB.jpeg"),
        ("Venice Grand Canal", "https://upload.wikimedia.org/wikipedia/commons/4/4a/Venice_-_Grand_Canal.jpg"),
    ],
    "United Kingdom": [
        ("Big Ben", "https://upload.wikimedia.org/wikipedia/commons/9/93/Clock_Tower_-_Palace_of_Westminster%2C_London_-_May_2007.jpg"),
        ("Stonehenge", "https://upload.wikimedia.org/wikipedia/commons/3/3c/Stonehenge2007_07_30.jpg"),
        ("Tower Bridge", "https://upload.wikimedia.org/wikipedia/commons/6/63/Tower_Bridge_from_Shad_Thames.jpg"),
    ],
    "Spain": [
        ("Sagrada Familia", "https://upload.wikimedia.org/wikipedia/commons/f/f4/Sagrada_Familia_nave_roof_detail.jpg"),
        ("Alhambra", "https://upload.wikimedia.org/wikipedia/commons/d/de/Dawn_Charles_V_702702.jpg"),
    ],
    "Greece": [
        ("Acropolis", "https://upload.wikimedia.org/wikipedia/commons/d/d4/The_Parthenon_in_Athens.jpg"),
        ("Santorini", "https://upload.wikimedia.org/wikipedia/commons/3/37/Ia_Santorini-2009-1.jpg"),
    ],
    "Germany": [
        ("Brandenburg Gate", "https://upload.wikimedia.org/wikipedia/commons/a/a6/Brandenburger_Tor_abends.jpg"),
        ("Neuschwanstein Castle", "https://upload.wikimedia.org/wikipedia/commons/f/f8/Schloss_Neuschwanstein_2013.jpg"),
    ],
    "Russia": [
        ("Saint Basil's Cathedral", "https://upload.wikimedia.org/wikipedia/commons/d/d5/Saint_Basil%27s_Cathedral_2023.jpg"),
        ("Kremlin", "https://upload.wikimedia.org/wikipedia/commons/a/af/Moscow_Kremlin-1.jpg"),
    ],
    "Netherlands": [
        ("Windmills of Kinderdijk", "https://upload.wikimedia.org/wikipedia/commons/7/7e/Kinderdijk_-_Molens_-_Overzicht.jpg"),
    ],
    "Czech Republic": [
        ("Charles Bridge", "https://upload.wikimedia.org/wikipedia/commons/1/19/Charles_Bridge%2C_Prague_-_02.jpg"),
    ],
    "Austria": [
        ("Schoenbrunn Palace", "https://upload.wikimedia.org/wikipedia/commons/1/1f/Schoenbrunn_Panorama_fr.jpg"),
    ],
    "Norway": [
        ("Geirangerfjord", "https://upload.wikimedia.org/wikipedia/commons/e/e5/Geirangerfjorden.jpg"),
    ],
    "Croatia": [
        ("Dubrovnik Old Town", "https://upload.wikimedia.org/wikipedia/commons/7/7a/Dubrovnik_crop.jpg"),
    ],
    "Turkey": [
        ("Hagia Sophia", "https://upload.wikimedia.org/wikipedia/commons/2/22/Hagia_Sophia_Mars_2013.jpg"),
        ("Cappadocia", "https://upload.wikimedia.org/wikipedia/commons/6/6d/Cappadocia_ballance_1.jpg"),
    ],
    "Hungary": [
        ("Hungarian Parliament Building", "https://upload.wikimedia.org/wikipedia/commons/7/75/Budapest_Parliament_4604.jpg"),
    ],
    "Portugal": [
        ("Tower of Belem", "https://upload.wikimedia.org/wikipedia/commons/3/34/Torre_Bel%C3%A9m_April_2009-4a.jpg"),
    ],
    "Switzerland": [
        ("Matterhorn", "https://upload.wikimedia.org/wikipedia/commons/e/e7/Matterhorn_from_Domh%C3%BCtte_-_2.jpg"),
    ],

    # ── Asia (~15) ──────────────────────────────────────────────────────────
    "China": [
        ("Great Wall of China", "https://upload.wikimedia.org/wikipedia/commons/2/23/The_Great_Wall_of_China_at_Jinshanling-edit.jpg"),
        ("Forbidden City", "https://upload.wikimedia.org/wikipedia/commons/4/49/Forbidden_City_Beijing_Shenwumen_Gate.jpg"),
        ("Terracotta Army", "https://upload.wikimedia.org/wikipedia/commons/4/49/Terracotta_Army%2C_View_of_Pit_1.jpg"),
    ],
    "India": [
        ("Taj Mahal", "https://upload.wikimedia.org/wikipedia/commons/b/bd/Taj_Mahal%2C_Agra%2C_India_edit3.jpg"),
        ("Hawa Mahal", "https://upload.wikimedia.org/wikipedia/commons/e/e3/Hawa_Mahal_2011.jpg"),
    ],
    "Japan": [
        ("Mount Fuji", "https://upload.wikimedia.org/wikipedia/commons/1/1b/080103_hakridge_fuji.jpg"),
        ("Fushimi Inari Shrine", "https://upload.wikimedia.org/wikipedia/commons/4/4b/Fushimi_Inari-taisha_Torii_2016.jpg"),
        ("Tokyo Tower", "https://upload.wikimedia.org/wikipedia/commons/3/37/TaroTokyo20110213-TokyoTower-01min.jpg"),
    ],
    "Cambodia": [
        ("Angkor Wat", "https://upload.wikimedia.org/wikipedia/commons/4/44/Ankor_Wat_temple.jpg"),
    ],
    "Malaysia": [
        ("Petronas Towers", "https://upload.wikimedia.org/wikipedia/commons/8/85/Petronas_Panorama_II.jpg"),
    ],
    "Indonesia": [
        ("Borobudur", "https://upload.wikimedia.org/wikipedia/commons/8/8c/Borobudur-Nothwest-view.jpg"),
    ],
    "Thailand": [
        ("Wat Arun", "https://upload.wikimedia.org/wikipedia/commons/7/78/Wat_Arun_Bangkok%2C_Thailand.jpg"),
    ],
    "Jordan": [
        ("Petra", "https://upload.wikimedia.org/wikipedia/commons/4/4b/Al_Khazneh_Petra_Edit_2.jpg"),
    ],
    "United Arab Emirates": [
        ("Burj Khalifa", "https://upload.wikimedia.org/wikipedia/commons/9/93/Burj_Khalifa.jpg"),
    ],
    "Israel": [
        ("Western Wall", "https://upload.wikimedia.org/wikipedia/commons/5/58/Jerusalem_Western_Wall_BW_1.JPG"),
    ],
    "South Korea": [
        ("Gyeongbokgung Palace", "https://upload.wikimedia.org/wikipedia/commons/1/10/Gyeongbok-gung_Palace%2C_Seoul.jpg"),
    ],
    "Vietnam": [
        ("Ha Long Bay", "https://upload.wikimedia.org/wikipedia/commons/0/05/Ha_Long_Bay%2C_Vietnam.jpg"),
    ],
    "Iran": [
        ("Persepolis", "https://upload.wikimedia.org/wikipedia/commons/0/0d/Persepolis_T_Chipiez.jpg"),
    ],

    # ── Americas (~15) ──────────────────────────────────────────────────────
    "United States": [
        ("Statue of Liberty", "https://upload.wikimedia.org/wikipedia/commons/a/a1/Statue_of_Liberty_7.jpg"),
        ("Grand Canyon", "https://upload.wikimedia.org/wikipedia/commons/a/aa/Dawn_on_the_S_rim_of_the_Grand_Canyon_%288645178272%29.jpg"),
        ("Golden Gate Bridge", "https://upload.wikimedia.org/wikipedia/commons/0/0c/GoldenGateBridge-001.jpg"),
        ("Mount Rushmore", "https://upload.wikimedia.org/wikipedia/commons/f/f3/Dean_Franklin_-_06.04.03_Mount_Rushmore_Monument_%28by-sa%29-3_new.jpg"),
    ],
    "Peru": [
        ("Machu Picchu", "https://upload.wikimedia.org/wikipedia/commons/e/eb/Machu_Picchu%2C_Peru.jpg"),
    ],
    "Brazil": [
        ("Christ the Redeemer", "https://upload.wikimedia.org/wikipedia/commons/4/4f/Christ_the_Redeemer_-_Cristo_Redentor.jpg"),
        ("Iguazu Falls", "https://upload.wikimedia.org/wikipedia/commons/b/b1/Iguazu_D%C3%A9cembre_2007_-_Panorama_3.jpg"),
    ],
    "Mexico": [
        ("Chichen Itza", "https://upload.wikimedia.org/wikipedia/commons/1/10/Chichen_Itza_3.jpg"),
        ("Teotihuacan", "https://upload.wikimedia.org/wikipedia/commons/c/cc/Piramide_del_Sol.jpg"),
    ],
    "Canada": [
        ("CN Tower", "https://upload.wikimedia.org/wikipedia/commons/9/96/Toronto_-_ON_-_CN_Tower_bei_Nacht2.jpg"),
        ("Niagara Falls", "https://upload.wikimedia.org/wikipedia/commons/a/ab/3Falls_Niagara.jpg"),
    ],
    "Chile": [
        ("Moai Statues", "https://upload.wikimedia.org/wikipedia/commons/1/14/AhuTongariki.jpg"),
    ],
    "Argentina": [
        ("Perito Moreno Glacier", "https://upload.wikimedia.org/wikipedia/commons/3/3a/Perito_Moreno_Glacier_Patagonia_Argentina_Luca_Galuzzi_2005.JPG"),
    ],
    "Colombia": [
        ("Cartagena Walled City", "https://upload.wikimedia.org/wikipedia/commons/a/a1/Walls_of_Cartagena.jpg"),
    ],
    "Cuba": [
        ("El Capitolio", "https://upload.wikimedia.org/wikipedia/commons/5/59/El_Capitolio_Havana.jpg"),
    ],
    "Guatemala": [
        ("Tikal", "https://upload.wikimedia.org/wikipedia/commons/a/ae/Tikal_mridge.jpg"),
    ],

    # ── Africa (~10) ────────────────────────────────────────────────────────
    "Egypt": [
        ("Pyramids of Giza", "https://upload.wikimedia.org/wikipedia/commons/e/e3/Kheops-Pyramid.jpg"),
        ("Abu Simbel", "https://upload.wikimedia.org/wikipedia/commons/6/66/Abu_Simbel%2C_Ramesses_Temple%2C_front%2C_Egypt%2C_Oct_2004.jpg"),
    ],
    "South Africa": [
        ("Table Mountain", "https://upload.wikimedia.org/wikipedia/commons/5/59/Table_Mountain_DanieVDM.jpg"),
    ],
    "Tanzania": [
        ("Mount Kilimanjaro", "https://upload.wikimedia.org/wikipedia/commons/6/6b/Mt._Kilimanjaro_12.2006.JPG"),
        ("Serengeti", "https://upload.wikimedia.org/wikipedia/commons/f/ff/Serengeti-African-Elephants.JPG"),
    ],
    "Morocco": [
        ("Hassan II Mosque", "https://upload.wikimedia.org/wikipedia/commons/9/9f/Hassan_II_mosque%2C_Casablanca.jpg"),
    ],
    "Ethiopia": [
        ("Rock-Hewn Churches of Lalibela", "https://upload.wikimedia.org/wikipedia/commons/e/ec/Bete_Giyorgis_03.jpg"),
    ],
    "Kenya": [
        ("Masai Mara", "https://upload.wikimedia.org/wikipedia/commons/6/63/Masai_Mara_at_Sunset.jpg"),
    ],
    "Zimbabwe": [
        ("Victoria Falls", "https://upload.wikimedia.org/wikipedia/commons/5/57/Victoriafalls.jpg"),
    ],
    "Madagascar": [
        ("Avenue of the Baobabs", "https://upload.wikimedia.org/wikipedia/commons/6/6a/Adansonia_grandidieri04.jpg"),
    ],

    # ── Oceania (~5) ────────────────────────────────────────────────────────
    "Australia": [
        ("Sydney Opera House", "https://upload.wikimedia.org/wikipedia/commons/a/a0/Sydney_Australia._%2821339175489%29.jpg"),
        ("Uluru", "https://upload.wikimedia.org/wikipedia/commons/3/3e/Uluru_%28Ayers_Rock%29%2C_Pair_2_-_Dec_2008.jpg"),
        ("Great Barrier Reef", "https://upload.wikimedia.org/wikipedia/commons/e/e1/GreatBarrierReef-EO.JPG"),
    ],
    "New Zealand": [
        ("Milford Sound", "https://upload.wikimedia.org/wikipedia/commons/c/c4/Milford_Sound_%28New_Zealand%29.JPG"),
    ],
    "Fiji": [
        ("Yasawa Islands", "https://upload.wikimedia.org/wikipedia/commons/a/ab/Yasawa_Islands_Fiji.jpg"),
    ],
}

# ── Thumbnail conversion ───────────────────────────────────────────────────
# Direct Wikimedia originals can be enormous (60+ MB).  Discord's embed proxy
# times out on them, so we convert to 800 px thumbnails via thumb.php.

_THUMB_WIDTH = 800

def _to_thumbnail(url: str) -> str:
    """Convert a direct Wikimedia Commons URL to an 800 px thumbnail.

    Input:  https://upload.wikimedia.org/wikipedia/commons/{a}/{ab}/{filename}
    Output: https://commons.wikimedia.org/w/thumb.php?f={filename}&w=800
    """
    prefix = "https://upload.wikimedia.org/wikipedia/commons/"
    if url.startswith(prefix):
        # Extract filename — last path segment
        filename = url.rsplit("/", 1)[-1]
        return f"https://commons.wikimedia.org/w/thumb.php?f={filename}&w={_THUMB_WIDTH}"
    return url  # non-Wikimedia URL, leave as-is


# ── Flat pool for question picking ──────────────────────────────────────────
# (landmark_name, country, thumbnail_url)
_LANDMARK_POOL: list[tuple[str, str, str]] = []
for _country, _landmarks in LANDMARKS.items():
    for _name, _url in _landmarks:
        _LANDMARK_POOL.append((_name, _country, _to_thumbnail(_url)))
