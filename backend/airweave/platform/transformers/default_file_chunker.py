"""Default file transformer using Chonkie for improved semantic chunking."""

import asyncio
import os

import aiofiles
from chonkie import RecursiveChunker, RecursiveLevel, RecursiveRules, SemanticChunker

from airweave.core.logging import logger
from airweave.platform.decorators import transformer
from airweave.platform.entities._base import ChunkEntity, FileEntity, ParentEntity
from airweave.platform.file_handling.conversion.factory import document_converter
from airweave.platform.sync.async_helpers import run_in_thread_pool
from airweave.platform.transformers.utils import (
    MARGIN_OF_ERROR,
    MAX_CHUNK_SIZE,
    METADATA_SIZE,
    count_tokens,
)

# Module-level shared chunker and cache
_shared_semantic_chunker = None
_semantic_chunker_cache = {}
_cache_lock = None

# Flag to control which chunking approach to use
USE_OPTIMIZED_CHUNKING = True  # Set to True to use faster token-based chunking

# Calculate safe chunk size accounting for metadata overhead
SAFE_CHUNK_SIZE = MAX_CHUNK_SIZE - METADATA_SIZE - MARGIN_OF_ERROR  # 8191 - 1200 - 250 = 6741


def get_recursive_chunker():
    """Create a RecursiveChunker for markdown with custom rules."""
    # Use a very large value to focus on structure rather than size
    structure_based_size = 1000000  # Effectively unlimited

    # Custom markdown chunking rules
    custom_rules = RecursiveRules(
        [
            RecursiveLevel(
                delimiters=["\n# "],
                include_delim="next",  # Include the delimiter in the next chunk
            ),
            RecursiveLevel(delimiters=["\n## "], include_delim="next"),
            RecursiveLevel(delimiters=["\n### "], include_delim="next"),
            RecursiveLevel(delimiters=["\n\n"], include_delim="next"),
            RecursiveLevel(delimiters=["\n- ", "\n* ", "\n1. "], include_delim="next"),
            RecursiveLevel(delimiters=["```"], include_delim="both"),
            RecursiveLevel(delimiters=[". ", "? ", "! "], include_delim="prev"),
        ]
    )

    return RecursiveChunker(
        tokenizer_or_token_counter=count_tokens,
        chunk_size=structure_based_size,
        rules=custom_rules,
        min_characters_per_chunk=100,
    )


async def get_optimized_semantic_chunker(
    max_chunk_size: int = SAFE_CHUNK_SIZE, entity_context: str = ""
):
    """Get an optimized semantic chunker with caching and connection pooling.

    This creates chunkers in a pool to avoid repeated initialization overhead.
    """
    global _semantic_chunker_cache, _cache_lock

    # Initialize cache lock if needed
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()

    cache_key = f"semantic_{max_chunk_size}"

    async with _cache_lock:
        if cache_key not in _semantic_chunker_cache:
            logger.info(
                f"🧠 CHUNKER_CACHE_MISS [{entity_context}] Creating new semantic chunker for cache"
            )

            def _create_semantic_chunker():
                return SemanticChunker(
                    embedding_model="text-embedding-ada-002",
                    chunk_size=max_chunk_size,
                    threshold=0.5,
                    mode="window",
                    min_sentences=1,
                    similarity_window=2,
                )

            # Create in thread pool
            chunker = await run_in_thread_pool(_create_semantic_chunker)
            _semantic_chunker_cache[cache_key] = chunker
            logger.info(f"🧠 CHUNKER_CACHE_ADD [{entity_context}] Added chunker to cache")
        else:
            logger.info(f"🧠 CHUNKER_CACHE_HIT [{entity_context}] Using cached semantic chunker")

    return _semantic_chunker_cache[cache_key]


async def _process_file_content(file: FileEntity, entity_context: str) -> str:
    """Process file content and convert to text if needed."""
    if not file.local_path:
        logger.error(f"📂 CHUNKER_NO_PATH [{entity_context}] File has no local path")
        return ""

    _, extension = os.path.splitext(file.local_path)
    extension = extension.lower()

    if extension == ".md":
        logger.info(f"📑 CHUNKER_READ_MD [{entity_context}] Reading markdown file directly")
        async with aiofiles.open(file.local_path, "r", encoding="utf-8") as f:
            content = await f.read()
        logger.info(f"📖 CHUNKER_READ_DONE [{entity_context}] Read {len(content)} characters")
        return content
    else:
        logger.info(f"🔄 CHUNKER_CONVERT [{entity_context}] Converting file to markdown")
        result = await document_converter.convert(file.local_path)
        if not result or not result.text_content:
            logger.warning(f"🚫 CHUNKER_CONVERT_EMPTY [{entity_context}] No content extracted")
            return ""
        logger.info(
            f"✅ CHUNKER_CONVERT_DONE [{entity_context}] "
            f"Converted to {len(result.text_content)} characters"
        )
        return result.text_content


async def _chunk_text_content(text_content: str, entity_context: str) -> list[str]:
    """Chunk text content using recursive and semantic chunkers with optimization."""
    # Use optimized chunking if flag is set
    if USE_OPTIMIZED_CHUNKING:
        logger.info(
            f"🚀 CHUNKER_OPTIMIZED_MODE [{entity_context}] Using optimized token-based chunking"
        )

        # Import the optimized chunking function
        from airweave.platform.transformers.optimized_file_chunker import _chunk_text_optimized

        return await _chunk_text_optimized(text_content, entity_context)

    # Original semantic chunking approach
    logger.info(f"🔧 CHUNKER_RECURSIVE_START [{entity_context}] Starting recursive chunking")

    recursive_chunker = get_recursive_chunker()
    initial_chunks = await run_in_thread_pool(recursive_chunker.chunk, text_content)

    logger.info(
        f"📝 CHUNKER_RECURSIVE_DONE [{entity_context}] Created {len(initial_chunks)} initial chunks"
    )

    final_chunk_texts = []
    large_chunks = 0

    # Pre-load semantic chunker if we anticipate needing it
    semantic_chunker = None
    large_chunk_count = sum(1 for chunk in initial_chunks if chunk.token_count > SAFE_CHUNK_SIZE)

    if large_chunk_count > 0:
        logger.info(
            f"🧠 CHUNKER_SEMANTIC_PRELOAD [{entity_context}] "
            f"Pre-loading semantic chunker for {large_chunk_count} large chunks"
        )
        semantic_chunker = await get_optimized_semantic_chunker(SAFE_CHUNK_SIZE, entity_context)

    for i, chunk in enumerate(initial_chunks):
        if chunk.token_count <= SAFE_CHUNK_SIZE:
            final_chunk_texts.append(chunk.text)
        else:
            large_chunks += 1
            logger.info(
                f"✂️  CHUNKER_SEMANTIC_SPLIT [{entity_context}] Chunk {i + 1} too large "
                f"({chunk.token_count} tokens), applying semantic chunking"
            )

            # Apply semantic chunking with optimized processing
            chunk_start = asyncio.get_event_loop().time()

            def _semantic_chunk_optimized(chunker, text, context):
                """Optimized semantic chunking with progress reporting."""
                logger.info(
                    f"🔧 CHUNKER_SEMANTIC_PROCESSING [{context}] Processing {len(text)} characters"
                )

                try:
                    result = chunker.chunk(text)
                    logger.info(
                        f"🔧 CHUNKER_SEMANTIC_INTERNAL_DONE [{context}] Internal chunking complete"
                    )
                    return result
                except Exception as e:
                    logger.error(
                        f"💥 CHUNKER_SEMANTIC_ERROR [{context}] Semantic chunking failed: {str(e)}"
                    )
                    # Fallback: split text manually into smaller parts
                    text_length = len(text)
                    part_size = text_length // 3  # Split into 3 parts as fallback
                    parts = [text[i : i + part_size] for i in range(0, text_length, part_size)]

                    # Create mock chunk objects
                    class MockChunk:
                        def __init__(self, text):
                            self.text = text

                    return [MockChunk(part) for part in parts if part.strip()]

            semantic_chunks = await run_in_thread_pool(
                _semantic_chunk_optimized, semantic_chunker, chunk.text, entity_context
            )

            chunk_elapsed = asyncio.get_event_loop().time() - chunk_start

            final_chunk_texts.extend([sc.text for sc in semantic_chunks])

            logger.info(
                f"📦 CHUNKER_SEMANTIC_RESULT [{entity_context}] Large chunk split into "
                f"{len(semantic_chunks)} semantic chunks in {chunk_elapsed:.3f}s"
            )

            # Yield control after processing each large chunk
            await asyncio.sleep(0)

    if large_chunks > 0:
        logger.info(
            f"🔄 CHUNKER_SEMANTIC_SUMMARY [{entity_context}] Applied semantic chunking to "
            f"{large_chunks} large chunks"
        )

    return final_chunk_texts


@transformer(name="File Chunker")
async def file_chunker(file: FileEntity) -> list[ParentEntity | ChunkEntity]:
    """Default file chunker that converts files to markdown chunks using Chonkie.

    This transformer:
    1. Takes a FileEntity as input
    2. Converts the file to markdown using AsyncMarkItDown (or reads directly if already markdown)
    3. Uses Chonkie for intelligent chunking with a two-step approach:
       - First uses RecursiveChunker with markdown rules
       - Then applies semantic chunking if chunks are too large
    4. Yields each chunk as a ChunkEntity

    Args:
        file: The FileEntity to process

    Returns:
        list[ParentEntity | ChunkEntity]: The processed chunks
    """
    entity_context = f"Entity({file.entity_id})"

    logger.info(
        f"📄 CHUNKER_START [{entity_context}] Starting file chunking for: {file.name} "
        f"(type: {type(file).__name__})"
    )

    file_class = type(file)
    produced_entities = []

    FileParentClass, FileChunkClass = file_class.create_parent_chunk_models()

    try:
        # Process file content
        logger.info(f"🔍 CHUNKER_PROCESS [{entity_context}] Processing file content")
        start_time = asyncio.get_event_loop().time()

        text_content = await _process_file_content(file, entity_context)

        process_elapsed = asyncio.get_event_loop().time() - start_time

        if not text_content or not text_content.strip():
            logger.warning(f"📭 CHUNKER_EMPTY [{entity_context}] No text content found")
            return []

        content_length = len(text_content)
        logger.info(
            f"📊 CHUNKER_CONTENT [{entity_context}] Processed {content_length} characters "
            f"in {process_elapsed:.2f}s"
        )

        # Chunk the content
        logger.info(f"✂️  CHUNKER_SPLIT_START [{entity_context}] Starting text chunking")
        chunk_start = asyncio.get_event_loop().time()

        final_chunk_texts = await _chunk_text_content(text_content, entity_context)

        chunk_elapsed = asyncio.get_event_loop().time() - chunk_start
        logger.info(
            f"📦 CHUNKER_SPLIT_DONE [{entity_context}] Created {len(final_chunk_texts)} chunks "
            f"in {chunk_elapsed:.2f}s"
        )

        # Create entities
        logger.info(
            f"🏗️  CHUNKER_ENTITIES_START [{entity_context}] Creating parent and chunk entities"
        )

        # Create parent entity for the file using all fields from original entity
        file_data = file.model_dump()
        file_data.update(
            {
                "number_of_chunks": len(final_chunk_texts),
            }
        )
        parent = FileParentClass(**file_data)
        produced_entities.append(parent)

        for i, chunk_text in enumerate(final_chunk_texts):
            if not chunk_text.strip():
                continue

            chunk = FileChunkClass(
                name=f"{file.name} - Chunk {i + 1}",
                entity_id=file.entity_id,
                sync_id=file.sync_id,
                parent_entity_id=parent.entity_id,
                parent_db_entity_id=parent.db_entity_id,
                md_content=chunk_text,
                md_type="text",
                md_position=i,
                md_parent_title=file.name,
                metadata={
                    "chunk_index": i,
                    "total_chunks": len(final_chunk_texts),
                },
            )
            produced_entities.append(chunk)

        total_elapsed = asyncio.get_event_loop().time() - start_time
        logger.info(
            f"✅ CHUNKER_COMPLETE [{entity_context}] Chunking completed in {total_elapsed:.2f}s "
            f"(1 parent + {len(final_chunk_texts)} chunks)"
        )

    except Exception as e:
        logger.error(
            f"💥 CHUNKER_ERROR [{entity_context}] Chunking failed: {type(e).__name__}: {str(e)}"
        )
        raise e

    return produced_entities
