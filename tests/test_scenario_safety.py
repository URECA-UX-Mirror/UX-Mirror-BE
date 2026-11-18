"""등록된 외부 시나리오 화면의 링크 검사. 기존 MORO MARKET 정책과 독립적이다."""
import pytest

from uxm.scenario_safety import SCENARIO_LINK_SELECTOR, ScenarioSafety


def candidate(**overrides):
    return {"tag": "a", "href": "/guide.html", "label": "안내", "onclick": None,
            "download": None, "target": None, "role": None, **overrides}


@pytest.fixture
def policy():
    return ScenarioSafety("https://example.test", ("/", "/guide.html", "/product/list.html?cate_no=23"))


@pytest.mark.parametrize("href", ["/guide.html", "guide.html", "https://example.test/guide.html"])
def test_registered_link_is_allowed(policy, href):
    assert policy.candidate_rejection("https://example.test/", candidate(href=href, label="이용안내")) is None


@pytest.mark.parametrize("href", [None, "", "#", "#tab", "javascript:alert(1)", "//other.test/guide.html",
                                  "https://example.test.evil.test/guide.html", "https://example.test@evil.test/guide.html",
                                  "http://example.test/guide.html", "/unknown", "/product/list.html?cate_no=24",
                                  "/product/list.html?cate_no=23&action=delete", "/myshop/index.html",
                                  "/a/%2e%2e/guide.html", "/%2fguide.html", "/guide.html?next=evil"])
def test_unregistered_or_ambiguous_links_are_rejected(policy, href):
    assert policy.candidate_rejection("https://example.test/", candidate(href=href)) is not None


def test_registered_query_is_not_dropped(policy):
    assert policy.url_rejection("https://example.test/", "/product/list.html?cate_no=23") is None
    assert policy.url_rejection("https://example.test/", "/product/list.html") == "OUT_OF_SCOPE"
    assert policy.arrival_rejection("https://example.test/product/list.html?cate_no=24") == "OUT_OF_SCOPE"


@pytest.mark.parametrize("tag", ["button", "input", "div", "form"])
def test_only_anchor_is_allowed(policy, tag):
    assert SCENARIO_LINK_SELECTOR == "a[href]"
    assert policy.candidate_rejection("https://example.test/", candidate(tag=tag)) == "NOT_LINK"


@pytest.mark.parametrize("extra", [{"onclick": "submit()"}, {"onclick": ""}, {"download": ""},
                                  {"target": "_blank"}, {"role": "button"}])
def test_anchor_with_action_or_new_context_is_rejected(policy, extra):
    element = candidate(**extra)
    assert policy.candidate_rejection("https://example.test/", element) == "UNSUPPORTED_LINK_ACTION"


def test_missing_dom_attributes_fail_closed(policy):
    assert policy.candidate_rejection("https://example.test/", {"tag": "a", "href": "/guide.html", "label": "안내"}) == "DOM_ATTRIBUTES_MISSING"


@pytest.mark.parametrize("label", ["구매하기", "바로 구매", "주문하기", "장바구니 담기", "로그아웃", "비우기", "CHECKOUT", "Log Out"])
def test_dangerous_labels_block_even_registered_destinations(policy, label):
    assert policy.candidate_rejection("https://example.test/", candidate(label=label)) == "DANGEROUS_ACTION"


@pytest.mark.parametrize("path", ["/exec/action", "/order/orderform.html", "/member/logout", "/logout"])
def test_dangerous_paths_rejected_even_if_registered(path):
    policy = ScenarioSafety("https://example.test", ("/", path))
    assert policy.url_rejection("https://example.test/", path) == "DANGEROUS_PATH"
    assert policy.arrival_rejection("https://example.test" + path) == "DANGEROUS_PATH"


def test_click_recheck_catches_changed_href_and_label(policy):
    original = candidate()
    assert policy.candidate_rejection("https://example.test/", original) is None
    assert policy.candidate_rejection("https://example.test/", {**original, "href": "https://evil.test/"}) == "OUT_OF_SCOPE"
    assert policy.candidate_rejection("https://example.test/", {**original, "label": "주문하기"}) == "DANGEROUS_ACTION"


def test_arrival_rechecks_redirect(policy):
    assert policy.arrival_rejection("https://example.test/guide.html") is None
    assert policy.arrival_rejection("https://other.test/guide.html") == "OUT_OF_SCOPE"
    assert policy.arrival_rejection("https://example.test/other") == "OUT_OF_SCOPE"


@pytest.mark.parametrize("base,pages", [("javascript:alert(1)", ("/",)), ("https://example.test", ()),
                                         ("https://example.test", ("https://evil.test/",)),
                                         ("https://example.test", ("/a#fragment",))])
def test_rejects_invalid_registration(base, pages):
    with pytest.raises(ValueError):
        ScenarioSafety(base, pages)
