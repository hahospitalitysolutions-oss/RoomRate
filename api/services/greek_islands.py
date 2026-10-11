"""Which Greek island a property is on, from its own coordinates.

Ferryhopper's port list has names only, no coordinates (probe 2026-10-11),
and a hotel's Booking city is often a village (Faliraki, Oia, Kardamena)
that no port is named after. The property's coordinates always come from
Booking, so the island is found by them, and the island's names find its
port.

Boxes are approximate (about ±1 km) and cover the inhabited islands with
ferry service most hotels sit on; where two overlap, the smaller wins, so a
small island next to a big one keeps its own hotels. Crete is split by
prefecture, each with its own main ports. A property outside every box is
treated as mainland, or an island reached by road (Evia, Lefkada).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Island:
    label: str
    # Latin names a port of the island may carry, the island's own first.
    names: tuple[str, ...]
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.lat_min <= latitude <= self.lat_max and self.lon_min <= longitude <= self.lon_max

    @property
    def area(self) -> float:
        return (self.lat_max - self.lat_min) * (self.lon_max - self.lon_min)


ISLANDS: tuple[Island, ...] = (
    # Dodecanese
    Island("Ρόδος", ("rodos", "rhodes"), 35.87, 36.47, 27.68, 28.25),
    Island("Κως", ("kos",), 36.65, 36.92, 26.93, 27.37),
    Island("Κάλυμνος", ("kalymnos", "pothia"), 36.92, 37.08, 26.88, 27.05),
    Island("Λέρος", ("leros", "lakki"), 37.08, 37.20, 26.80, 26.90),
    Island("Πάτμος", ("patmos", "skala"), 37.27, 37.37, 26.52, 26.62),
    Island("Λειψοί", ("lipsi", "leipsoi"), 37.28, 37.32, 26.72, 26.80),
    Island("Κάρπαθος", ("karpathos", "pigadia", "diafani"), 35.40, 35.90, 27.05, 27.25),
    Island("Σύμη", ("symi",), 36.53, 36.65, 27.78, 27.90),
    Island("Τήλος", ("tilos",), 36.38, 36.47, 27.33, 27.45),
    Island("Νίσυρος", ("nisyros",), 36.55, 36.63, 27.12, 27.22),
    Island("Αστυπάλαια", ("astypalaia", "astypalea"), 36.50, 36.65, 26.20, 26.45),
    Island("Χάλκη", ("chalki", "halki"), 36.20, 36.25, 27.55, 27.63),
    Island("Κάσος", ("kasos",), 35.35, 35.43, 26.85, 26.98),
    Island("Καστελλόριζο", ("kastellorizo", "megisti"), 36.13, 36.16, 29.56, 29.62),
    # Cyclades
    Island("Μύκονος", ("mykonos",), 37.39, 37.50, 25.30, 25.47),
    Island("Σαντορίνη", ("santorini", "thira", "thera", "fira"), 36.33, 36.49, 25.33, 25.49),
    Island("Πάρος", ("paros", "parikia"), 36.98, 37.16, 25.10, 25.28),
    Island("Αντίπαρος", ("antiparos",), 36.95, 37.08, 24.97, 25.10),
    Island("Νάξος", ("naxos",), 36.94, 37.20, 25.35, 25.60),
    Island("Ίος", ("ios",), 36.67, 36.78, 25.25, 25.42),
    Island("Μήλος", ("milos", "adamas"), 36.63, 36.78, 24.30, 24.55),
    Island("Κίμωλος", ("kimolos",), 36.77, 36.84, 24.52, 24.60),
    Island("Σύρος", ("syros", "ermoupoli"), 37.35, 37.52, 24.87, 24.98),
    Island("Τήνος", ("tinos",), 37.52, 37.67, 25.03, 25.27),
    Island("Άνδρος", ("andros", "gavrio"), 37.73, 37.98, 24.68, 24.98),
    Island("Σίφνος", ("sifnos", "kamares"), 36.93, 37.03, 24.65, 24.75),
    Island("Σέριφος", ("serifos",), 37.10, 37.22, 24.42, 24.55),
    Island("Κύθνος", ("kythnos",), 37.35, 37.47, 24.37, 24.47),
    Island("Κέα", ("kea", "tzia", "korissia"), 37.55, 37.70, 24.27, 24.40),
    Island("Φολέγανδρος", ("folegandros",), 36.58, 36.66, 24.85, 25.00),
    Island("Σίκινος", ("sikinos",), 36.66, 36.72, 25.05, 25.17),
    Island("Αμοργός", ("amorgos", "katapola", "aegiali"), 36.78, 36.93, 25.75, 26.00),
    Island("Κουφονήσια", ("koufonisia", "koufonisi"), 36.92, 36.96, 25.57, 25.63),
    Island("Ανάφη", ("anafi",), 36.33, 36.38, 25.73, 25.83),
    # Saronic
    Island("Αίγινα", ("aegina", "aigina"), 37.67, 37.78, 23.42, 23.58),
    Island("Αγκίστρι", ("agistri", "angistri"), 37.68, 37.72, 23.32, 23.37),
    Island("Πόρος", ("poros",), 37.48, 37.54, 23.44, 23.53),
    Island("Ύδρα", ("hydra", "ydra"), 37.30, 37.38, 23.38, 23.58),
    Island("Σπέτσες", ("spetses",), 37.22, 37.29, 23.08, 23.18),
    # East edge before Perama (23.57), on the mainland across the strait.
    Island("Σαλαμίνα", ("salamina", "paloukia", "selinia"), 37.87, 38.00, 23.42, 23.55),
    # Sporades
    Island("Σκιάθος", ("skiathos",), 39.13, 39.20, 23.38, 23.52),
    Island("Σκόπελος", ("skopelos", "glossa"), 39.05, 39.20, 23.60, 23.77),
    Island("Αλόννησος", ("alonissos", "alonnisos", "patitiri"), 39.10, 39.25, 23.83, 24.02),
    Island("Σκύρος", ("skyros", "linaria"), 38.77, 38.98, 24.40, 24.65),
    # Ionian
    Island("Κέρκυρα", ("kerkyra", "corfu"), 39.35, 39.83, 19.63, 20.12),
    Island("Παξοί", ("paxos", "paxoi", "gaios"), 39.18, 39.24, 20.13, 20.24),
    Island("Κεφαλονιά", ("kefalonia", "kefallonia", "cephalonia"), 38.03, 38.50, 20.33, 20.83),
    Island("Ιθάκη", ("ithaki", "ithaca"), 38.33, 38.50, 20.62, 20.77),
    Island("Ζάκυνθος", ("zakynthos", "zante"), 37.64, 37.95, 20.61, 21.00),
    Island("Κύθηρα", ("kythira", "kythera", "diakofti"), 36.13, 36.40, 22.88, 23.10),
    # North and East Aegean
    Island("Λέσβος", ("mytilini", "mytilene", "lesvos", "lesbos"), 38.95, 39.40, 25.83, 26.62),
    Island("Χίος", ("chios",), 38.15, 38.62, 25.83, 26.17),
    Island("Σάμος", ("samos", "vathi", "karlovasi", "pythagorio"), 37.65, 37.82, 26.55, 27.08),
    Island("Ικαρία", ("ikaria", "agios kirykos", "evdilos"), 37.53, 37.70, 25.95, 26.37),
    Island("Λήμνος", ("limnos", "lemnos", "myrina"), 39.78, 40.03, 25.05, 25.45),
    Island("Θάσος", ("thasos", "limenas", "prinos"), 40.55, 40.81, 24.52, 24.80),
    Island("Σαμοθράκη", ("samothraki", "samothrace"), 40.40, 40.52, 25.43, 25.70),
    # Crete, by prefecture: each part's own main ports.
    Island("Χανιά", ("chania", "souda", "kissamos"), 35.15, 35.70, 23.50, 24.30),
    Island("Ρέθυμνο", ("rethymno", "rethymnon"), 35.05, 35.45, 24.30, 24.75),
    Island("Ηράκλειο", ("heraklion", "irakleio", "iraklion"), 34.90, 35.45, 24.75, 25.40),
    Island("Λασίθι", ("agios nikolaos", "sitia"), 34.90, 35.35, 25.40, 26.35),
)


def island_at(latitude: float | None, longitude: float | None) -> Island | None:
    """The island whose box holds the point; the smallest on an overlap; None off every box."""
    if latitude is None or longitude is None:
        return None
    holding = [island for island in ISLANDS if island.contains(float(latitude), float(longitude))]
    return min(holding, key=lambda island: island.area) if holding else None
