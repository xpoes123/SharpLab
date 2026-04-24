"""Landmark dataset for the geography game's Landmarks mode.

Each entry maps a country to a list of (landmark_name, image_url) tuples.
Image URLs use Wikimedia Commons /thumb/ format at 800px width (public domain / CC).
"""

LANDMARKS: dict[str, list[tuple[str, str]]] = {
    # ── Europe (~20) ────────────────────────────────────────────────────────
    "France": [
        ("Eiffel Tower", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a8/Tour_Eiffel_Wikimedia_Commons.jpg/800px-Tour_Eiffel_Wikimedia_Commons.jpg"),
        ("Mont Saint-Michel", "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4a/Mont_Saint_Michel_France.jpg/800px-Mont_Saint_Michel_France.jpg"),
    ],
    "Italy": [
        ("Colosseum", "https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/Colosseo_2020.jpg/800px-Colosseo_2020.jpg"),
        ("Leaning Tower of Pisa", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/The_Leaning_Tower_of_Pisa_SB.jpeg/800px-The_Leaning_Tower_of_Pisa_SB.jpeg"),
        ("Venice Grand Canal", "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4a/Venice_-_Grand_Canal.jpg/800px-Venice_-_Grand_Canal.jpg"),
    ],
    "United Kingdom": [
        ("Big Ben", "https://upload.wikimedia.org/wikipedia/commons/thumb/9/93/Clock_Tower_-_Palace_of_Westminster%2C_London_-_May_2007.jpg/800px-Clock_Tower_-_Palace_of_Westminster%2C_London_-_May_2007.jpg"),
        ("Stonehenge", "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3c/Stonehenge2007_07_30.jpg/800px-Stonehenge2007_07_30.jpg"),
        ("Tower Bridge", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/63/Tower_Bridge_from_Shad_Thames.jpg/800px-Tower_Bridge_from_Shad_Thames.jpg"),
    ],
    "Spain": [
        ("Sagrada Familia", "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f4/Sagrada_Familia_nave_roof_detail.jpg/800px-Sagrada_Familia_nave_roof_detail.jpg"),
        ("Alhambra", "https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/Dawn_Charles_V_702702.jpg/800px-Dawn_Charles_V_702702.jpg"),
    ],
    "Greece": [
        ("Acropolis", "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d4/The_Parthenon_in_Athens.jpg/800px-The_Parthenon_in_Athens.jpg"),
        ("Santorini", "https://upload.wikimedia.org/wikipedia/commons/thumb/3/37/Ia_Santorini-2009-1.jpg/800px-Ia_Santorini-2009-1.jpg"),
    ],
    "Germany": [
        ("Brandenburg Gate", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a6/Brandenburger_Tor_abends.jpg/800px-Brandenburger_Tor_abends.jpg"),
        ("Neuschwanstein Castle", "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f8/Schloss_Neuschwanstein_2013.jpg/800px-Schloss_Neuschwanstein_2013.jpg"),
    ],
    "Russia": [
        ("Saint Basil's Cathedral", "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d5/Saint_Basil%27s_Cathedral_2023.jpg/800px-Saint_Basil%27s_Cathedral_2023.jpg"),
        ("Kremlin", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/af/Moscow_Kremlin-1.jpg/800px-Moscow_Kremlin-1.jpg"),
    ],
    "Netherlands": [
        ("Windmills of Kinderdijk", "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7e/Kinderdijk_-_Molens_-_Overzicht.jpg/800px-Kinderdijk_-_Molens_-_Overzicht.jpg"),
    ],
    "Czech Republic": [
        ("Charles Bridge", "https://upload.wikimedia.org/wikipedia/commons/thumb/1/19/Charles_Bridge%2C_Prague_-_02.jpg/800px-Charles_Bridge%2C_Prague_-_02.jpg"),
    ],
    "Austria": [
        ("Schoenbrunn Palace", "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1f/Schoenbrunn_Panorama_fr.jpg/800px-Schoenbrunn_Panorama_fr.jpg"),
    ],
    "Norway": [
        ("Geirangerfjord", "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e5/Geirangerfjorden.jpg/800px-Geirangerfjorden.jpg"),
    ],
    "Croatia": [
        ("Dubrovnik Old Town", "https://upload.wikimedia.org/wikipedia/commons/thumb/7/7a/Dubrovnik_crop.jpg/800px-Dubrovnik_crop.jpg"),
    ],
    "Turkey": [
        ("Hagia Sophia", "https://upload.wikimedia.org/wikipedia/commons/thumb/2/22/Hagia_Sophia_Mars_2013.jpg/800px-Hagia_Sophia_Mars_2013.jpg"),
        ("Cappadocia", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6d/Cappadocia_ballance_1.jpg/800px-Cappadocia_ballance_1.jpg"),
    ],
    "Hungary": [
        ("Hungarian Parliament Building", "https://upload.wikimedia.org/wikipedia/commons/thumb/7/75/Budapest_Parliament_4604.jpg/800px-Budapest_Parliament_4604.jpg"),
    ],
    "Portugal": [
        ("Tower of Belem", "https://upload.wikimedia.org/wikipedia/commons/thumb/3/34/Torre_Bel%C3%A9m_April_2009-4a.jpg/800px-Torre_Bel%C3%A9m_April_2009-4a.jpg"),
    ],
    "Switzerland": [
        ("Matterhorn", "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/Matterhorn_from_Domh%C3%BCtte_-_2.jpg/800px-Matterhorn_from_Domh%C3%BCtte_-_2.jpg"),
    ],

    # ── Asia (~15) ──────────────────────────────────────────────────────────
    "China": [
        ("Great Wall of China", "https://upload.wikimedia.org/wikipedia/commons/thumb/2/23/The_Great_Wall_of_China_at_Jinshanling-edit.jpg/800px-The_Great_Wall_of_China_at_Jinshanling-edit.jpg"),
        ("Forbidden City", "https://upload.wikimedia.org/wikipedia/commons/thumb/4/49/Forbidden_City_Beijing_Shenwumen_Gate.jpg/800px-Forbidden_City_Beijing_Shenwumen_Gate.jpg"),
        ("Terracotta Army", "https://upload.wikimedia.org/wikipedia/commons/thumb/4/49/Terracotta_Army%2C_View_of_Pit_1.jpg/800px-Terracotta_Army%2C_View_of_Pit_1.jpg"),
    ],
    "India": [
        ("Taj Mahal", "https://upload.wikimedia.org/wikipedia/commons/thumb/b/bd/Taj_Mahal%2C_Agra%2C_India_edit3.jpg/800px-Taj_Mahal%2C_Agra%2C_India_edit3.jpg"),
        ("Hawa Mahal", "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Hawa_Mahal_2011.jpg/800px-Hawa_Mahal_2011.jpg"),
    ],
    "Japan": [
        ("Mount Fuji", "https://upload.wikimedia.org/wikipedia/commons/thumb/1/1b/080103_hakridge_fuji.jpg/800px-080103_hakridge_fuji.jpg"),
        ("Fushimi Inari Shrine", "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Fushimi_Inari-taisha_Torii_2016.jpg/800px-Fushimi_Inari-taisha_Torii_2016.jpg"),
        ("Tokyo Tower", "https://upload.wikimedia.org/wikipedia/commons/thumb/3/37/TaroTokyo20110213-TokyoTower-01min.jpg/800px-TaroTokyo20110213-TokyoTower-01min.jpg"),
    ],
    "Cambodia": [
        ("Angkor Wat", "https://upload.wikimedia.org/wikipedia/commons/thumb/4/44/Ankor_Wat_temple.jpg/800px-Ankor_Wat_temple.jpg"),
    ],
    "Malaysia": [
        ("Petronas Towers", "https://upload.wikimedia.org/wikipedia/commons/thumb/8/85/Petronas_Panorama_II.jpg/800px-Petronas_Panorama_II.jpg"),
    ],
    "Indonesia": [
        ("Borobudur", "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/Borobudur-Nothwest-view.jpg/800px-Borobudur-Nothwest-view.jpg"),
    ],
    "Thailand": [
        ("Wat Arun", "https://upload.wikimedia.org/wikipedia/commons/thumb/7/78/Wat_Arun_Bangkok%2C_Thailand.jpg/800px-Wat_Arun_Bangkok%2C_Thailand.jpg"),
    ],
    "Jordan": [
        ("Petra", "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4b/Al_Khazneh_Petra_Edit_2.jpg/800px-Al_Khazneh_Petra_Edit_2.jpg"),
    ],
    "United Arab Emirates": [
        ("Burj Khalifa", "https://upload.wikimedia.org/wikipedia/commons/thumb/9/93/Burj_Khalifa.jpg/800px-Burj_Khalifa.jpg"),
    ],
    "Israel": [
        ("Western Wall", "https://upload.wikimedia.org/wikipedia/commons/thumb/5/58/Jerusalem_Western_Wall_BW_1.JPG/800px-Jerusalem_Western_Wall_BW_1.JPG"),
    ],
    "South Korea": [
        ("Gyeongbokgung Palace", "https://upload.wikimedia.org/wikipedia/commons/thumb/1/10/Gyeongbok-gung_Palace%2C_Seoul.jpg/800px-Gyeongbok-gung_Palace%2C_Seoul.jpg"),
    ],
    "Vietnam": [
        ("Ha Long Bay", "https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/Ha_Long_Bay%2C_Vietnam.jpg/800px-Ha_Long_Bay%2C_Vietnam.jpg"),
    ],
    "Iran": [
        ("Persepolis", "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/Persepolis_T_Chipiez.jpg/800px-Persepolis_T_Chipiez.jpg"),
    ],

    # ── Americas (~15) ──────────────────────────────────────────────────────
    "United States": [
        ("Statue of Liberty", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Statue_of_Liberty_7.jpg/800px-Statue_of_Liberty_7.jpg"),
        ("Grand Canyon", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/aa/Dawn_on_the_S_rim_of_the_Grand_Canyon_%288645178272%29.jpg/800px-Dawn_on_the_S_rim_of_the_Grand_Canyon_%288645178272%29.jpg"),
        ("Golden Gate Bridge", "https://upload.wikimedia.org/wikipedia/commons/thumb/0/0c/GoldenGateBridge-001.jpg/800px-GoldenGateBridge-001.jpg"),
        ("Mount Rushmore", "https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/Dean_Franklin_-_06.04.03_Mount_Rushmore_Monument_%28by-sa%29-3_new.jpg/800px-Dean_Franklin_-_06.04.03_Mount_Rushmore_Monument_%28by-sa%29-3_new.jpg"),
    ],
    "Peru": [
        ("Machu Picchu", "https://upload.wikimedia.org/wikipedia/commons/thumb/e/eb/Machu_Picchu%2C_Peru.jpg/800px-Machu_Picchu%2C_Peru.jpg"),
    ],
    "Brazil": [
        ("Christ the Redeemer", "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4f/Christ_the_Redeemer_-_Cristo_Redentor.jpg/800px-Christ_the_Redeemer_-_Cristo_Redentor.jpg"),
        ("Iguazu Falls", "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b1/Iguazu_D%C3%A9cembre_2007_-_Panorama_3.jpg/800px-Iguazu_D%C3%A9cembre_2007_-_Panorama_3.jpg"),
    ],
    "Mexico": [
        ("Chichen Itza", "https://upload.wikimedia.org/wikipedia/commons/thumb/1/10/Chichen_Itza_3.jpg/800px-Chichen_Itza_3.jpg"),
        ("Teotihuacan", "https://upload.wikimedia.org/wikipedia/commons/thumb/c/cc/Piramide_del_Sol.jpg/800px-Piramide_del_Sol.jpg"),
    ],
    "Canada": [
        ("CN Tower", "https://upload.wikimedia.org/wikipedia/commons/thumb/9/96/Toronto_-_ON_-_CN_Tower_bei_Nacht2.jpg/800px-Toronto_-_ON_-_CN_Tower_bei_Nacht2.jpg"),
        ("Niagara Falls", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/3Falls_Niagara.jpg/800px-3Falls_Niagara.jpg"),
    ],
    "Chile": [
        ("Moai Statues", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a2/Moai_Rano_rarridge.jpg/800px-Moai_Rano_rarridge.jpg"),
    ],
    "Argentina": [
        ("Perito Moreno Glacier", "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/Perito_Moreno_Glacier_Patagonia_Argentina_Luca_Galuzzi_2005.JPG/800px-Perito_Moreno_Glacier_Patagonia_Argentina_Luca_Galuzzi_2005.JPG"),
    ],
    "Colombia": [
        ("Cartagena Walled City", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a1/Walls_of_Cartagena.jpg/800px-Walls_of_Cartagena.jpg"),
    ],
    "Cuba": [
        ("El Capitolio", "https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/El_Capitolio_Havana.jpg/800px-El_Capitolio_Havana.jpg"),
    ],
    "Guatemala": [
        ("Tikal", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ae/Tikal_mridge.jpg/800px-Tikal_mridge.jpg"),
    ],

    # ── Africa (~10) ────────────────────────────────────────────────────────
    "Egypt": [
        ("Pyramids of Giza", "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Kheops-Pyramid.jpg/800px-Kheops-Pyramid.jpg"),
        ("Abu Simbel", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/66/Abu_Simbel%2C_Ramesses_Temple%2C_front%2C_Egypt%2C_Oct_2004.jpg/800px-Abu_Simbel%2C_Ramesses_Temple%2C_front%2C_Egypt%2C_Oct_2004.jpg"),
    ],
    "South Africa": [
        ("Table Mountain", "https://upload.wikimedia.org/wikipedia/commons/thumb/5/59/Table_Mountain_DanieVDM.jpg/800px-Table_Mountain_DanieVDM.jpg"),
    ],
    "Tanzania": [
        ("Mount Kilimanjaro", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6b/Mt._Kilimanjaro_12.2006.JPG/800px-Mt._Kilimanjaro_12.2006.JPG"),
        ("Serengeti", "https://upload.wikimedia.org/wikipedia/commons/thumb/f/ff/Serengeti-African-Elephants.JPG/800px-Serengeti-African-Elephants.JPG"),
    ],
    "Morocco": [
        ("Hassan II Mosque", "https://upload.wikimedia.org/wikipedia/commons/thumb/9/9f/Hassan_II_mosque%2C_Casablanca.jpg/800px-Hassan_II_mosque%2C_Casablanca.jpg"),
    ],
    "Ethiopia": [
        ("Rock-Hewn Churches of Lalibela", "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ec/Bete_Giyorgis_03.jpg/800px-Bete_Giyorgis_03.jpg"),
    ],
    "Kenya": [
        ("Masai Mara", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/63/Masai_Mara_at_Sunset.jpg/800px-Masai_Mara_at_Sunset.jpg"),
    ],
    "Zimbabwe": [
        ("Victoria Falls", "https://upload.wikimedia.org/wikipedia/commons/thumb/5/57/Victoriafalls.jpg/800px-Victoriafalls.jpg"),
    ],
    "Madagascar": [
        ("Avenue of the Baobabs", "https://upload.wikimedia.org/wikipedia/commons/thumb/6/6a/Adansonia_grandidieri04.jpg/800px-Adansonia_grandidieri04.jpg"),
    ],

    # ── Oceania (~5) ────────────────────────────────────────────────────────
    "Australia": [
        ("Sydney Opera House", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/a0/Sydney_Australia._%2821339175489%29.jpg/800px-Sydney_Australia._%2821339175489%29.jpg"),
        ("Uluru", "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Uluru_%28Ayers_Rock%29%2C_Pair_2_-_Dec_2008.jpg/800px-Uluru_%28Ayers_Rock%29%2C_Pair_2_-_Dec_2008.jpg"),
        ("Great Barrier Reef", "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e1/GreatBarrierReef-EO.JPG/800px-GreatBarrierReef-EO.JPG"),
    ],
    "New Zealand": [
        ("Milford Sound", "https://upload.wikimedia.org/wikipedia/commons/thumb/c/c4/Milford_Sound_%28New_Zealand%29.JPG/800px-Milford_Sound_%28New_Zealand%29.JPG"),
    ],
    "Fiji": [
        ("Yasawa Islands", "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Yasawa_Islands_Fiji.jpg/800px-Yasawa_Islands_Fiji.jpg"),
    ],
}

# ── Flat pool for question picking ──────────────────────────────────────────
# (landmark_name, country, image_url)
_LANDMARK_POOL: list[tuple[str, str, str]] = []
for _country, _landmarks in LANDMARKS.items():
    for _name, _url in _landmarks:
        _LANDMARK_POOL.append((_name, _country, _url))
