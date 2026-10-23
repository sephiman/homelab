# Coding Standards & Guidelines

These rules apply across all repositories in `~/IdeaProjects` and `~/PycharmProjects`.

## 1. Testing Environment Principles
- This machine is a testing and development environment. Live bots and production data run on a separate deploy host.
- Reading, querying, `docker exec`, and running backtests or scripts against local test containers is allowed.
- Prefer Testcontainers or throwaway stacks for test suites and migrations so local testing data stays intact. Do not drop or wipe local test databases unasked.
- Never point anything at real production hosts or databases.

## 2. Clean Code & Architecture
- **Package by Feature:** Group by domain/feature (`position/`, `identity/`, `sync/`, `exams/`), never by technical layer (`services/`, `utils/`, `helpers/`).
- **Small, Single-Purpose Units:** Each function does one thing; each class/module owns a single concern.
- **Meaningful Naming:** No vague names (`data`, `manager`, `helper`, `process()`). If a comment is needed to explain what a name does, rename it.
- **Guard Clauses:** Favor early returns and guard clauses over deeply nested `if/else` blocks. Keep the primary path at the leftmost indentation.
- **Immutability by Default:** Use immutable primitives (`val`, `data class`, `const`, frozen dataclasses / Pydantic models). Never mutate input arguments.
- **No Dead Code:** Delete obsolete code instead of commenting it out. Do not leave `v2` copies beside the original.
- **No Speculative Generality:** Build specifically for the problem at hand; generalize only on the second real caller.
- **Match Surrounding Idiom:** Consistency with existing repository idioms supersedes personal preference.

## 3. Design Patterns
- **Composition over Inheritance:** Prefer dependency injection (FastAPI `Depends`, Spring beans) over inheritance hierarchies.
- **Strategy Pattern:** Use strategy implementations over sprawling `switch`/`when`/`if-elif` chains (e.g. one connector per exchange, one generator per problem type).
- **Domain Boundaries:** Keep business and domain logic out of API controllers, routers, and frontend UI components. Controllers map HTTP; components render; domain services encapsulate rules.
- **Explicit DTO Boundaries:** Never leak internal database models (JPA entities, SQLAlchemy models) directly to external APIs. Always map to/from explicit DTOs or Pydantic schemas.
- **Centralized Error Handling:** Use typed domain exceptions handled centrally via global exception handlers, rather than repetitive ad-hoc `try/except` blocks.

## 4. Documentation & Comments
- **Minimal Docstrings:** At most one line, and only when the *why* is truly non-obvious. Avoid multi-paragraph documentation blocks.
- **Surprise-Only Comments:** If an explanation is needed, place a concise inline comment (`#` or `//`) directly at the surprising line.
- **No Redundant Boilerplate:** Never restate signatures, `@param`, `@return`, `Args:`, or `Returns:` if type hints and signatures already make them clear.
