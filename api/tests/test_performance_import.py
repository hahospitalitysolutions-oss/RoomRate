"""Reading the owners' bookings files: the RoomRate template and the Booking.com export, .xlsx and .csv."""

import csv
import importlib.util
import io
from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from api.services.performance.importing import reader
from api.services.performance.demo import generate_demo_bookings
from api.services.performance.amounts import net_amount
from api.services.performance.importing import ImportFileError, read_bookings_file
from api.services.performance.importing.labels import label_counts
from api.services.performance.importing.values import parse_amount, parse_date, parse_status
from api.services.performance.template import build_template

AS_OF = date(2026, 10, 20)
FIELDS = (
    "booking_ref", "room_label", "status", "booked_on", "cancelled_on", "arrival", "departure", "rooms",
    "adults", "children", "gross_eur", "commission_eur", "country_code", "channel",
)


@pytest.fixture(scope="module")
def demo():
    return generate_demo_bookings(seasons=(2025, 2026, 2027), as_of=AS_OF, seed=5)


@pytest.fixture(scope="module")
def files(demo, tmp_path_factory):
    path = Path(__file__).resolve().parents[2] / "scripts" / "generate_demo_bookings.py"
    spec = importlib.util.spec_from_file_location("generate_demo_bookings", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script.write_files(demo, tmp_path_factory.mktemp("demo"))


def project(booking):
    return {key: booking[key] for key in FIELDS}


def xlsx(rows):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_csv(rows, delimiter=",", encoding="utf-8-sig"):
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter)
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode(encoding)


EXPORT_HEADER = ["Book number", "Guest name(s)", "Check-in", "Check-out", "Booked on", "Status", "Rooms",
                 "Adults", "Children", "Price", "Commission amount", "Booker country", "Unit type", "Phone number"]


def export_row(**values):
    row = {
        "Book number": "4011", "Guest name(s)": "Maria Papadopoulou", "Check-in": "2026-07-01",
        "Check-out": "2026-07-04", "Booked on": "2026-03-01 12:00:00", "Status": "ok", "Rooms": "1",
        "Adults": "2", "Children": "0", "Price": "300.00 EUR", "Commission amount": "51.00 EUR",
        "Booker country": "de", "Unit type": "Double Room", "Phone number": "+30 690 000 0000",
    }
    row.update(values)
    return [row[column] for column in EXPORT_HEADER]


# ---------------------------------------------------------------------------
# Both formats give back exactly what was generated
# ---------------------------------------------------------------------------


def test_template_round_trip(demo, files):
    parsed = read_bookings_file(files["template"].read_bytes(), "demo_template.xlsx", "template")

    assert parsed.format == "template" and parsed.errors == []
    assert [project(booking) for booking in parsed.bookings] == [project(booking) for booking in demo.bookings]
    assert [(room["room_label"], room["rooms"]) for room in parsed.rooms] == [
        (room.label, room.rooms) for room in demo.room_types
    ]
    assert [(c["date_from"], c["date_to"], c["room_label"], c["reason"]) for c in parsed.closures] == [
        (c["date_from"], c["date_to"], None, "Εκτός σεζόν") for c in demo.closures
    ]


@pytest.mark.parametrize("name", ["export_xlsx", "export_csv"])
def test_booking_export_round_trip(demo, files, name):
    content = files[name].read_bytes()
    parsed = read_bookings_file(content, files[name].name, "booking")
    expected = [booking for booking in demo.bookings if booking["channel"] == "Booking.com"]

    assert parsed.format == "booking" and parsed.errors == []
    assert [project(booking) for booking in parsed.bookings] == [project(booking) for booking in expected]


def test_guest_columns_are_never_read(files):
    parsed = read_bookings_file(files["export_xlsx"].read_bytes(), "export.xlsx", "booking")

    text = repr(parsed)
    assert "Demo Guest" not in text and "+00 000" not in text


def test_a_file_in_the_other_format_is_read_as_that_format(files):
    parsed = read_bookings_file(files["export_csv"].read_bytes(), "export.csv", "template")

    assert parsed.format == "booking" and parsed.bookings


def test_the_template_examples_are_skipped():
    content = build_template([{"label": "Double Room", "rooms": 3}])
    parsed = read_bookings_file(content, "RoomRate_kratiseis.xlsx", "template")

    assert parsed.bookings == [] and parsed.errors == []
    assert parsed.rooms == [{"room_label": "Double Room", "rooms": 3, "valid_from": None, "valid_to": None}]
    assert label_counts(parsed.bookings, [room["room_label"] for room in parsed.rooms]) == {"Double Room": 0}


# ---------------------------------------------------------------------------
# Rows that cannot be imported: Greek message, Excel row number
# ---------------------------------------------------------------------------


def test_errors_name_the_excel_row_even_below_a_title():
    rows = [
        ["Reservations export"],
        [],
        EXPORT_HEADER,
        export_row(),
        export_row(**{"Book number": "4012", "Check-out": ""}),
        export_row(**{"Book number": "4013", "Check-out": "2026-06-29"}),
        export_row(**{"Book number": "4014", "Price": "τριακόσια"}),
        export_row(**{"Book number": "4015", "Check-in": "31/02/2026"}),
        export_row(**{"Book number": "4016", "Status": "maybe"}),
        export_row(**{"Book number": "4017", "Price": "300 USD"}),
        export_row(**{"Book number": "4018", "Booked on": "2026-08-01"}),
        export_row(**{"Book number": "", "Unit type": "Twin"}),
    ]
    parsed = read_bookings_file(xlsx(rows), "export.xlsx", "booking")

    assert [booking["booking_ref"] for booking in parsed.bookings] == ["4011"]
    assert parsed.errors == [
        {"row": 5, "booking_ref": "4012", "message": "Λείπει η ημερομηνία αναχώρησης."},
        {"row": 6, "booking_ref": "4013", "message": "Η αναχώρηση είναι πριν από την άφιξη."},
        {"row": 7, "booking_ref": "4014", "message": "Το ποσό δεν είναι αριθμός («τριακόσια»)."},
        {"row": 8, "booking_ref": "4015", "message": "Η άφιξη δεν είναι ημερομηνία («31/02/2026»)."},
        {"row": 9, "booking_ref": "4016",
         "message": "Άγνωστη κατάσταση («maybe»): Επιβεβαιωμένη, Ακυρωμένη ή No-show."},
        {"row": 10, "booking_ref": "4017", "message": "Το ποσό δεν είναι σε ευρώ («300 USD»)."},
        {"row": 11, "booking_ref": "4018", "message": "Η ημερομηνία κράτησης είναι μετά την άφιξη."},
        {"row": 12, "booking_ref": None, "message": "Λείπει ο αριθμός κράτησης."},
    ]


def test_a_repeated_booking_number_keeps_the_last_row():
    rows = [EXPORT_HEADER, export_row(Price="100 EUR"), export_row(Price="120 EUR")]
    parsed = read_bookings_file(xlsx(rows), "export.xlsx", "booking")

    assert [booking["gross_eur"] for booking in parsed.bookings] == [120.0]
    assert parsed.errors == [{
        "row": 2, "booking_ref": "4011",
        "message": "Ο ίδιος αριθμός κράτησης υπάρχει και στη γραμμή 3· κρατήθηκε εκείνη.",
    }]


def test_template_sheet_errors_say_which_sheet():
    content = build_template([{"label": "Double Room", "rooms": None}], closures=[
        {"date_from": date(2026, 11, 1), "date_to": date(2026, 10, 1), "reason": "Λάθος"},
    ])
    parsed = read_bookings_file(content, "t.xlsx", "template")

    assert {error["message"] for error in parsed.errors} == {
        "Φύλλο «Δωμάτια»: «Double Room»: λείπει ο αριθμός δωματίων.",
        "Φύλλο «Κλειστές περίοδοι»: το «Έως» είναι πριν από το «Από».",
    }


# ---------------------------------------------------------------------------
# What a real file may look like
# ---------------------------------------------------------------------------


def test_semicolon_greek_csv_with_decimal_commas():
    header = ["Αριθμός κράτησης", "Άφιξη", "Αναχώρηση", "Τύπος δωματίου", "Συνολικό ποσό (€)",
              "Προμήθεια καναλιού (€)", "Κανάλι", "Κατάσταση", "Χώρα πελάτη"]
    rows = [header, ["A-1", "01/07/2026", "05/07/2026", "Δίκλινο", "1.234,50", "0", "Τηλέφωνο", "Ακυρωμένη", "Γαλλία"]]
    parsed = read_bookings_file(export_csv(rows, delimiter=";", encoding="cp1253"), "kratiseis.csv", "template")

    (booking,) = parsed.bookings
    assert booking["gross_eur"] == 1234.5 and booking["status"] == "cancelled"
    assert booking["channel"] == "Τηλέφωνο" and booking["country_code"] == "FR"
    assert (booking["arrival"], booking["departure"]) == (date(2026, 7, 1), date(2026, 7, 5))


def test_export_fills_departure_from_nights_and_commission_from_its_percentage():
    header = ["Book number", "Check-in", "Duration (nights)", "Unit type", "Price", "Commission %", "People", "Children"]
    rows = [header, ["9", datetime(2026, 8, 1), 3, "Suite", 400, 15, 3, 1]]
    (booking,) = read_bookings_file(xlsx(rows), "e.xlsx", "booking").bookings

    assert booking["departure"] == date(2026, 8, 4)
    assert booking["commission_eur"] == 60.0
    assert (booking["adults"], booking["children"]) == (2, 1)
    assert booking["channel"] == "Booking.com" and booking["status"] == "confirmed"


def test_several_room_types_in_one_booking_become_one_booking_each():
    rows = [
        EXPORT_HEADER,
        export_row(**{"Rooms": "2", "Unit type": "Double Room, Twin Room", "Price": "300.01 EUR", "Adults": "3"}),
        export_row(**{"Book number": "4012", "Rooms": "2", "Unit type": "Double Room, Double Room"}),
    ]
    parsed = read_bookings_file(xlsx(rows), "e.xlsx", "booking")
    first, second, third = parsed.bookings

    assert (first["booking_ref"], first["room_label"], first["rooms"]) == ("4011-1", "Double Room", 1)
    assert (second["booking_ref"], second["room_label"]) == ("4011-2", "Twin Room")
    assert first["gross_eur"] + second["gross_eur"] == pytest.approx(300.01)
    assert first["commission_eur"] + second["commission_eur"] == pytest.approx(51.0)
    assert first["adults"] + second["adults"] == 3
    assert (third["booking_ref"], third["room_label"], third["rooms"]) == ("4012", "Double Room", 2)


def test_excel_booking_numbers_stay_whole():
    rows = [EXPORT_HEADER, export_row(**{"Book number": 4018833201.0})]

    assert read_bookings_file(xlsx(rows), "e.xlsx", "booking").bookings[0]["booking_ref"] == "4018833201"


# ---------------------------------------------------------------------------
# Files that cannot be read at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content, filename, message",
    [
        (b"", "x.csv", "Το αρχείο είναι άδειο."),
        (b"\xd0\xcf\x11\xe0rest", "export.xls", "παλιού τύπου Excel (.xls)"),
        (b"%PDF-1.7", "export.pdf", "δεν είναι .xlsx ή .csv"),
        (b"PK\x03\x04broken", "export.xlsx", "Το αρχείο Excel δεν διαβάζεται"),
    ],
)
def test_unreadable_files(content, filename, message):
    with pytest.raises(ImportFileError, match=message.replace("(", r"\(").replace(")", r"\)").replace(".", r"\.")):
        read_bookings_file(content, filename, "booking")


def test_a_file_without_the_columns_names_the_missing_ones():
    with pytest.raises(ImportFileError) as error:
        read_bookings_file(export_csv([["Book number", "Unit type"], ["1", "Double"]]), "x.csv", "booking")

    assert str(error.value) == (
        "Δεν βρέθηκαν οι στήλες: Άφιξη, Ποσό. Ανεβάστε το export κρατήσεων του extranet όπως κατέβηκε."
    )


def test_too_many_rows(monkeypatch):
    monkeypatch.setattr(reader, "MAX_ROWS", 2)
    rows = [EXPORT_HEADER] + [export_row(**{"Book number": str(n)}) for n in range(3)]

    with pytest.raises(ImportFileError, match="Το αρχείο έχει 3 κρατήσεις· το όριο είναι 2."):
        read_bookings_file(xlsx(rows), "e.xlsx", "booking")


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("2026-07-14", date(2026, 7, 14)),
        ("2026-07-14 18:22:05", date(2026, 7, 14)),
        ("14/07/2026", date(2026, 7, 14)),
        ("4/7/2026", date(2026, 7, 4)),
        ("14.07.2026", date(2026, 7, 14)),
        ("14-07-2026", date(2026, 7, 14)),
        ("14 Jul 2026", date(2026, 7, 14)),
        ("Jul 14, 2026", date(2026, 7, 14)),
        (datetime(2026, 7, 14, 9, 30), date(2026, 7, 14)),
        (46217, date(2026, 7, 14)),
        ("", None),
        (None, None),
    ],
)
def test_parse_date(value, expected):
    assert parse_date(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (436, 436.0),
        ("436 EUR", 436.0),
        ("436.00 EUR", 436.0),
        ("€ 1.234,50", 1234.5),
        ("1,234.50", 1234.5),
        ("1.234", 1234.0),
        ("1,5", 1.5),
        ("12,25 €", 12.25),
        ("2 345,10", 2345.1),
        ("EUR 99", 99.0),
        ("", None),
    ],
)
def test_parse_amount(value, expected):
    assert parse_amount(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("ok", "confirmed"), ("OK", "confirmed"), ("", "confirmed"), ("Επιβεβαιωμένη", "confirmed"),
        ("cancelled_by_guest", "cancelled"), ("Cancelled by hotel", "cancelled"), ("Ακυρωμένη", "cancelled"),
        ("no_show", "no_show"), ("No-show", "no_show"), ("No show", "no_show"),
    ],
)
def test_parse_status(value, expected):
    assert parse_status(value) == expected


def test_net_takes_off_the_fee_when_in_the_price_then_vat_then_commission():
    included = {"vat_pct": 13, "resilience_fee_eur": 1.5, "fee_included_in_price": True}
    kept = net_amount(339.0, 51.0, rooms=1, nights=3, settings=included)
    assert round(kept, 2) == round((339.0 - 4.5) / 1.13 - 51.0, 2)
    excluded = {**included, "fee_included_in_price": False}
    assert round(net_amount(339.0, 0, rooms=1, nights=3, settings=excluded), 2) == 300.0
