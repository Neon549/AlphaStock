"""Runtime entrypoint for the document-rag skill."""

from api.multimodal import extract_document_citations, retrieve_from_document


def run(*, session_id: str, query: str) -> dict:
    context = retrieve_from_document(session_id, query)
    return {
        "context": context,
        "citations": extract_document_citations(context) if context else [],
    }
