from alirag.router import route


def test_explicit_fast_malay():
    r = route("cepat: berapa final claim project Dawson?")
    assert r.mode == "FAST" and r.explicit
    assert r.cleaned_query.startswith("berapa")


def test_explicit_fast_english_prefix():
    assert route("fast: find LAI-003").mode == "FAST"
    assert route("quickly check drawing L201").mode == "FAST"


def test_explicit_deep():
    r = route("deep: why was this alternative rejected?")
    assert r.mode == "DEEP" and r.explicit


def test_explicit_fullswing_and_phd():
    assert route("fullswing: analyse the complete history of this claim").mode == "FULLSWING"
    assert route("phd: prove whether this technical argument is correct").mode == "FULLSWING"
    assert route("analisa habis semua dokumen tender").mode == "FULLSWING"


def test_document_intent_defaults_deep():
    assert route("why did we reject the contractor's alternative species?").mode == "DEEP"
    assert route("compare tender and submission for Zone B").mode == "DEEP"
    assert route("what does this document say about root barrier?").mode == "DEEP"


def test_complexity_auto_fullswing():
    assert route("reconstruct the complete timeline of NCR-014 across all projects").mode == "FULLSWING"


def test_default_fast_simple_lookup():
    r = route("find LAI-003")
    assert r.mode == "FAST" and not r.explicit
    assert "LAI-003" in r.exact_ids


def test_fast_wins_over_document_words():
    # explicit FAST beats document-intent (§8 precedence)
    assert route("cepat: what does the tender say about turf?").mode == "FAST"


def test_exact_ids_harvested():
    r = route("check KP-980ASPEN-CS-LANDSCAPE-26 and L-201")
    norm = [i.upper() for i in r.exact_ids]
    assert any("KP" in i for i in norm)
    assert any("L-201" in i or "L201" in i for i in norm)


def test_project_hint():
    r = route("berapa final claim untuk Dawson?", known_projects=["Dawson", "Meridian"])
    assert r.project_hint == "Dawson"
    r2 = route("total claim amount overall", known_projects=["Dawson", "Meridian"])
    assert r2.project_hint is None
