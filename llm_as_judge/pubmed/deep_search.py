from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import json
import os
import ssl
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass(frozen=True)
class Paper:
    pmid: str
    title: str
    abstract: str
    journal: str
    year: str | None
    authors: list[str]
    doi: str | None
    pmc_id: str | None
    url: str


class NCBIEutilsHttpClient:
    def __init__(
        self,
        base_url: str = EUTILS_BASE_URL,
        timeout: int = 30,
        *,
        verify_ssl: bool = True,
        urlopen_function=urlopen,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.urlopen_function = urlopen_function

    def get_json(self, endpoint: str, params: dict[str, str | int]) -> dict[str, Any]:
        text = self.get_text(endpoint, {**params, "retmode": "json"})
        return json.loads(text)

    def get_text(self, endpoint: str, params: dict[str, str | int]) -> str:
        url = f"{self.base_url}/{endpoint}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": "llm-as-judge-pubmed-search/0.1"})
        context = None if self.verify_ssl else ssl._create_unverified_context()
        with self.urlopen_function(request, timeout=self.timeout, context=context) as response:
            return response.read().decode("utf-8")


class PubMedDeepSearcher:
    def __init__(
        self,
        *,
        email: str | None = None,
        api_key: str | None = None,
        tool: str = "llm-as-judge-pubmed-search",
        http_client=None,
    ):
        self.email = email or os.getenv("NCBI_EMAIL")
        self.api_key = api_key or os.getenv("NCBI_API_KEY")
        self.tool = tool
        self.http_client = http_client or NCBIEutilsHttpClient()

    def search(self, query: str, *, max_results: int = 20, sort: str = "relevance") -> list[Paper]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if max_results < 1:
            raise ValueError("max_results must be at least 1")

        search_params = self._base_params()
        search_params.update(
            {
                "db": "pubmed",
                "term": normalized_query,
                "retmax": max_results,
                "sort": sort,
                "usehistory": "n",
            }
        )
        search_payload = self.http_client.get_json("esearch.fcgi", search_params)
        pmids = search_payload.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []

        fetch_params = self._base_params()
        fetch_params.update(
            {
                "db": "pubmed",
                "id": ",".join(pmids),
                "rettype": "abstract",
                "retmode": "xml",
            }
        )
        xml_text = self.http_client.get_text("efetch.fcgi", fetch_params)
        return parse_pubmed_xml(xml_text)

    def _base_params(self) -> dict[str, str]:
        params = {"tool": self.tool}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params


def parse_pubmed_xml(xml_text: str) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers = []
    for article in root.findall(".//PubmedArticle"):
        pmid = _text(article, ".//MedlineCitation/PMID")
        if not pmid:
            continue
        papers.append(
            Paper(
                pmid=pmid,
                title=_clean_text(_text(article, ".//Article/ArticleTitle")),
                abstract=_extract_abstract(article),
                journal=_clean_text(_text(article, ".//Journal/Title")),
                year=_extract_year(article),
                authors=_extract_authors(article),
                doi=_extract_article_id(article, "doi"),
                pmc_id=_extract_article_id(article, "pmc"),
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            )
        )
    return papers


def build_literature_context(papers: list[Paper]) -> str:
    lines = ["Pipeline Stage: LLM Data Mining and Context Generation", ""]
    for index, paper in enumerate(papers, start=1):
        authors = ", ".join(paper.authors[:6]) if paper.authors else "Unknown authors"
        year = paper.year or "Unknown year"
        doi = f" DOI: {paper.doi}." if paper.doi else ""
        pmc = f" PMCID: {paper.pmc_id}." if paper.pmc_id else ""
        lines.extend(
            [
                f"{index}. {paper.title}",
                f"   Authors: {authors}. Journal: {paper.journal or 'Unknown journal'}. Year: {year}.",
                f"   PMID: {paper.pmid}.{doi}{pmc}",
                f"   URL: {paper.url}",
                f"   Abstract: {paper.abstract or 'No abstract available.'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _text(element: ET.Element, path: str) -> str:
    target = element.find(path)
    if target is None:
        return ""
    return "".join(target.itertext()).strip()


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _extract_abstract(article: ET.Element) -> str:
    parts = []
    for abstract_text in article.findall(".//Abstract/AbstractText"):
        label = abstract_text.attrib.get("Label")
        text = _clean_text("".join(abstract_text.itertext()))
        if not text:
            continue
        parts.append(f"{label}: {text}" if label else text)
    return " ".join(parts)


def _extract_year(article: ET.Element) -> str | None:
    for path in (
        ".//JournalIssue/PubDate/Year",
        ".//ArticleDate/Year",
        ".//PubMedPubDate[@PubStatus='pubmed']/Year",
    ):
        year = _text(article, path)
        if year:
            return year
    medline_date = _text(article, ".//JournalIssue/PubDate/MedlineDate")
    return medline_date[:4] if medline_date[:4].isdigit() else None


def _extract_authors(article: ET.Element) -> list[str]:
    authors = []
    for author in article.findall(".//AuthorList/Author"):
        collective_name = _text(author, "CollectiveName")
        if collective_name:
            authors.append(_clean_text(collective_name))
            continue
        last_name = _text(author, "LastName")
        initials = _text(author, "Initials")
        full_name = _clean_text(f"{last_name} {initials}".strip())
        if full_name:
            authors.append(full_name)
    return authors


def _extract_article_id(article: ET.Element, id_type: str) -> str | None:
    for article_id in article.findall(".//ArticleIdList/ArticleId"):
        if article_id.attrib.get("IdType") == id_type and article_id.text:
            return article_id.text.strip()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search PubMed and return structured paper metadata.")
    parser.add_argument("query", help="PubMed query string.")
    parser.add_argument("--max-results", type=int, default=10, help="Maximum number of PubMed records.")
    parser.add_argument("--email", default=os.getenv("NCBI_EMAIL"), help="NCBI contact email.")
    parser.add_argument("--api-key", default=os.getenv("NCBI_API_KEY"), help="Optional NCBI API key.")
    parser.add_argument("--format", choices=("json", "context"), default="context", help="Output format.")
    parser.add_argument(
        "--allow-insecure-ssl",
        action="store_true",
        help="Disable SSL certificate verification for controlled local smoke tests.",
    )
    args = parser.parse_args(argv)

    http_client = NCBIEutilsHttpClient(verify_ssl=not args.allow_insecure_ssl)
    searcher = PubMedDeepSearcher(email=args.email, api_key=args.api_key, http_client=http_client)
    papers = searcher.search(args.query, max_results=args.max_results)
    if args.format == "json":
        print(json.dumps([asdict(paper) for paper in papers], indent=2))
    else:
        print(build_literature_context(papers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
