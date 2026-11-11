"""Metro lines that are planned but not built, which OSM has no routes or stations for.
Each set is drawn as its own network (`projects` in regions.py), with its own toggle.

A line lists its stations in order: (name, (lon, lat)), or (name, None) for an open or
building station it starts from or passes (its position then comes from OSM). Its track
is the OSM `railway=proposed` way(s) named `track`, cut at its first and last stations
(Maia II's way also carries the metroBus spur to the airport, which is left out), or,
without one, a smooth curve through the stations. `km` is the official length.
`status` says how far along it is (the page explains each).

Metro do Porto 3.0 (Metro do Porto, October 2023: https://www.metrodoporto.pt/pages/630?news_id=488).
Stations are placed on the OSM track as follows:
- Gondomar II: from the Estudo Prévio in public consultation (participa.pt, Aug-Sep 2026), as
  reported station by station (Observador, 5 Aug 2026): São Roque on Alameda de Cartes, Cerco
  on Av. Cidade de León just past the Rotunda de Cartes, Parque Oriental at the roundabout of
  Av. Cidade de León and Av. Francisco Xavier Esteves, Lagoa north of Rua Luís de Camões and
  Rua da Capela da Lagoa, Valbom behind Rua Prof. Georgina Nunes das Neves Duarte and Rua
  Pinheiro d'Aquém, Hospital Fernando Pessoa, Cosme Ferreira Castro and Oliveira Martins on
  those streets, Souto by Gondomar's bus terminal. Each is the track point nearest the
  described spot (within 60 m, Parque Oriental 140 m).
- ISMAI-Muro (Trofa): the EIA in consultation (Aug-Oct 2026) has light rail from ISMAI to
  Muro (3.1 km, stations Ribela and Muro) and a metroBus from Muro to Trofa, left out here.
  Ribela at its bus stop (20 m from the track), Muro at the end of the track.
- São Mamede (IPO - Estádio do Mar, 6.55 km): stations on the 2023 map; IPO II, Elaine
  Sanceau, Xanana Gusmão and Senhora da Hora II at their places (within 15 m of the track),
  ISCAP by the institute, Pedra Verde by Rua 5 de Outubro (where the 2009 EIA put it);
  S. Mamede and S. Gens spaced between them as on the official map.
- Maia II, metro option (Roberto Frias - Os Verdes II, 13.01 km, 16 stations; a metroBus
  option is also studied): the OSM track measures 12.97 km to Verdes II. Roberto Frias, HSJ II,
  Pedrouços, Milheirós, Parque Maia II, Espido, Ponte de Moreira and Os Verdes II at their
  places (within 90 m); the others, known only by neighbourhood, spaced between them as on the
  official map.

PMUS do Porto (Plano de Mobilidade Urbana Sustentável, Câmara Municipal do Porto / OPT,
public consultation Sep-Nov 2026: https://mobilidade.cm-porto.pt/destaque/pmus-discussao-publica),
action TP.08 "Expansão da rede de Metro" (PMUS Medidas, pp. 100-102, and Fig. 57). Its
stations are read off Fig. 57, a map in ETRS89/PT-TM06, georeferenced on 28 open stations
drawn on it (median error 13 m). The plan names every station but those of the Galiza -
Estádio do Dragão study, named here after what the plan says they serve (Rua de Cedofeita,
Jardim Paulo Vallada).

The PMUS covers only Porto: its Campo Alegre line stops at Anémona (Praça Cidade do
Salvador), "at the gates of Matosinhos". Beyond it the line follows Metro do Porto's own
design of the Campo Alegre line (Estudo Prévio and EIA, January 2009, SIAIA AIA2063:
Matosinhos Sul - São Bento), the only plan for the Matosinhos side: on the surface along Rua
de Brito Capelo from Praça Cidade do Salvador to a Matosinhos Sul II station on that street,
linked to line A at Matosinhos Sul. No newer plan for it was found (Matosinhos's own project
there is a metroBus).
"""

PROJECTS = {
    "metro_3_0": [
        {"ref": "GO", "name": "Linha de Gondomar II", "colour": "#B8962E", "track": "Linha de Gondomar", "km": 6.9,
         "status": "eia", "stations": [
             ("Estádio do Dragão", None),
             ("São Roque", (-8.57521, 41.16269)),
             ("Cerco", (-8.57181, 41.15907)),
             ("Parque Oriental", (-8.56317, 41.15404)),
             ("Lagoa", (-8.5565, 41.1458)),
             ("Valbom", (-8.55335, 41.14064)),
             ("Hospital Fernando Pessoa", (-8.54433, 41.14232)),
             ("Cosme Ferreira Castro", (-8.5412, 41.14143)),
             ("Oliveira Martins", (-8.53728, 41.13811)),
             ("Souto", (-8.53, 41.13634)),
         ]},
        {"ref": "TR", "name": "Linha ISMAI – Muro – Trofa", "colour": "#8BC63E", "track": "Linha da Trofa", "km": 3.1,
         "status": "eia_trofa", "stations": [
             ("ISMAI", None),
             ("Ribela", (-8.60533, 41.28278)),
             ("Muro", (-8.60051, 41.29655)),
         ]},
        {"ref": "SM", "name": "Linha de São Mamede", "colour": "#A0522D", "track": "Linha de São Mamede", "km": 6.55,
         "status": "studies", "stations": [
             ("IPO II", (-8.60452, 41.1813)),
             ("ISCAP", (-8.60163, 41.18861)),
             ("S. Mamede", (-8.6126, 41.19008)),
             ("Pedra Verde", (-8.62157, 41.19077)),
             ("Elaine Sanceau", (-8.62951, 41.19102)),
             ("Xanana Gusmão", (-8.63799, 41.19011)),
             ("S. Gens", (-8.64546, 41.18896)),
             ("Senhora da Hora II", (-8.65368, 41.18771)),
             ("Estádio do Mar", None),
         ]},
        {"ref": "M2", "name": "Linha da Maia II", "colour": "#13959E", "track": "Linha da Maia II", "km": 13.01,
         "status": "studies_mode", "stations": [
             ("Roberto Frias", (-8.60014, 41.17591)),
             ("HSJ II", (-8.59778, 41.1808)),
             ("Pedrouços", (-8.58826, 41.18797)),
             ("Giesta", (-8.58602, 41.19127)),
             ("São Gemil", (-8.58248, 41.19727)),
             ("Caverneira", (-8.58223, 41.2022)),
             ("Águas Santas", (-8.58242, 41.20948)),
             ("Milheirós", (-8.59416, 41.21414)),
             ("Gueifães", (-8.60238, 41.21946)),
             ("Maninhos", (-8.60818, 41.22403)),
             ("Catassol", (-8.61263, 41.22591)),
             ("Chantre", (-8.6205, 41.22564)),
             ("Parque Maia II", (-8.62622, 41.22917)),
             ("Espido", (-8.63853, 41.23466)),
             ("Ponte de Moreira", (-8.64676, 41.23657)),
             ("Os Verdes II", (-8.65747, 41.23933)),
         ]},
    ],
    "pmus": [
        # To build within the plan (5-10 years).
        {"ref": "CA", "name": "Linha do Campo Alegre", "colour": "#6F6F6F", "km": 7.4, "status": "pmus_build",
         "note": "matosinhos_2009", "stations": [
            ("Galiza", None),
            ("Campo Alegre", None),
            ("Jardim Botânico", (-8.64189, 41.15341)),
            ("Lordelo do Ouro", (-8.64985, 41.15393)),
            ("Fluvial", (-8.65838, 41.15054)),
            ("Pasteleira", (-8.66631, 41.15098)),
            ("Império", (-8.67269, 41.15495)),
            ("Molhe", (-8.67902, 41.16108)),
            ("Parque da Cidade", (-8.68414, 41.16691)),
            ("Anémona", (-8.68827, 41.17328)),
            # Matosinhos: Metro do Porto's 2009 design (6.7 km to Anémona + 0.7 km)
            ("Matosinhos Sul II", (-8.68905, 41.1793)),
        ]},
        # Rosa extension: designs to be developed within the plan, in two phases.
        {"ref": "G1", "name": "Linha Rosa: Casa da Música – Roberto Frias", "colour": "#F48FB8", "km": 3.3,
         "status": "pmus_design", "stations": [
             ("Casa da Música", None),
             ("Carvalhido", (-8.62679, 41.16593)),
             ("Arca de Água", (-8.61984, 41.16849)),
             ("Nove de Abril", (-8.61203, 41.17215)),
             ("Pólo Universitário", None),
             ("Roberto Frias", (-8.5984, 41.17677)),
         ]},
        {"ref": "G2", "name": "Linha Rosa: Roberto Frias – Campanhã", "colour": "#F48FB8", "km": 3.5,
         "status": "pmus_design", "stations": [
             ("Roberto Frias", (-8.5984, 41.17677)),
             ("Igreja da Areosa", (-8.59015, 41.17508)),
             ("Conde Ferreira", (-8.58893, 41.16989)),
             ("Antas", (-8.59141, 41.16225)),
             ("Praça das Flores", (-8.59012, 41.1559)),
             ("Campanhã", None),
         ]},
        # Preliminary studies: closing the Rosa ring, Rubi to the north, Campo Alegre east.
        {"ref": "G3", "name": "Linha Rosa: Campanhã – São Bento", "colour": "#F48FB8", "km": 2.4,
         "status": "pmus_study", "stations": [
             ("Campanhã", None),
             ("Campo 24 de Agosto", None),
             ("Batalha", (-8.60346, 41.146)),
             ("São Bento", None),
         ]},
        {"ref": "H1", "name": "Linha Rubi: Casa da Música – Monte dos Burgos", "colour": "#9E1A4F",
         "status": "pmus_study", "stations": [
             ("Casa da Música", None),
             ("Carvalhido", (-8.62679, 41.16593)),
             ("Santa Luzia", (-8.626, 41.17435)),
             ("Monte dos Burgos", (-8.62854, 41.17965)),
         ]},
        {"ref": "CA2", "name": "Linha do Campo Alegre: Galiza – Estádio do Dragão", "colour": "#6F6F6F",
         "status": "pmus_study_gondomar", "stations": [
             ("Galiza", None),
             ("Cedofeita", (-8.61824, 41.15191)),
             ("Trindade", None),
             ("Paulo Vallada", (-8.59689, 41.15381)),
             ("Praça das Flores", (-8.59012, 41.1559)),
             ("Estádio do Dragão", None),
         ]},
    ],
}
