# RS || Spec Set structural and identifier rules

## RS-GLS || Define terms

| TERM | DEFINITION |
| --- | --- |
| section_heading | ATX heading outside fenced code that follows the normative heading grammar. |
| section | A section heading and its scoped content range. |
| leaf_section | A section with no descendant section heading. |
| direct_content | Section content excluding descendant section content. |
| pipe_table | Contiguous Markdown pipe table outside fenced code using the escape grammar in RS-TBL-PIPE. |
| requirements_table | Pipe table with the four reserved requirement columns. |
| registry_table | Pipe table that is not a requirements table. |
| requirement_group | Leaf H3-H5 section that directly scopes a requirements table. |
| project_requirement | Normative project constraint with truthful project ancestry. |
| placeholder_requirement_item | Template-mode Requirement Item containing authoring placeholders or a valid item code containing TBD. |
| framework_rule | Spec Set workflow, graph, structural, authoring, readiness, materialization, or validation rule owned by an authoritative framework file. |
| origin_document | Product Spec for one materialized project. |
| derived_document | Normative project document with immediate normative upstream documents. |
| template_mode | Validation mode allowing declared placeholders and authoring metadata. |
| materialized_mode | Validation mode requiring concrete project content and resolved references. |

## RS-MDL || Define the document model

Normative project documents use section headings, prose, registry tables, and Requirements Tables. Requirements Tables contain only project requirements. Generic development workflow belongs to [SDD/TDD Flow](../../../.work-models/flows/SDD_TDD.md) and its stage owners; [Spec Set integration](../../AGENTS.md) integrates Spec Set artifacts and service records. `SPEC_FILES_INDEX.specset.md` owns Spec Set topology, this ruleset owns shared syntax, and each `*.specset.template.md` owns its document-local rules. Validator code and tests consume these authorities.

Product Brief uses its template-local non-normative profile. Generated project manifests use the global manifest profile defined by the index. Section IDs, document keys, requirement IDs, and registry keys are distinct namespaces.

### RS-MDL-RFCM || Match RFC 2119 keywords

- The keyword set is `MUST`, `MUST NOT`, `REQUIRED`, `SHALL`, `SHALL NOT`, `SHOULD`, `SHOULD NOT`, `RECOMMENDED`, `MAY`, and `OPTIONAL`.
- Matching MUST be whole-word and all-caps.
- Two-token keywords match only when separated by ASCII whitespace.

## RS-PRF || Define profiles and modes

### RS-PRF-MODES || Define validation modes

- Template mode MUST enforce parser-visible structure, identifier grammar, table roles, declared namespace syntax, canonical link mirrors, authoring-contract schema, and declared deterministic readiness while applying `RS-TMP-PLCH`.
- Materialized mode MUST enforce concrete paths, reference resolution, canonical-index mirrors, deterministic readiness, applicability branches, and no-placeholder rules.

### RS-PRF-NORM || Define the normative project profile

The normative profile applies to Product Spec, Domain Spec, Technical Spec, UI Spec, Codebase Spec, Slices Map, and Slice specifications. Product Spec is the only normative Origin Document. All other normative project documents are Derived Documents.

### RS-PRF-MANF || Define the project manifest profile

A generated project manifest MAY use this heading grammar and Registry Tables. It MUST NOT contain Requirements Tables, project requirement IDs, or independently authored graph edges. Its file, role, status, and link data MUST mirror the canonical index.

## RS-SEC || Define section headings

### RS-SEC-FORM || Define heading syntax

- The first non-empty project-content line MUST be an H1 section heading.
- Every ATX heading in project content outside fenced code MUST match `<hashes> SP <SECTION_ID> SP || SP <SECTION_TITLE>`.
- Exactly one ASCII space MUST follow the hashes and appear on each side of `||`.
- Heading levels deeper than H5 MUST NOT appear outside fenced code.

### RS-SEC-RANG || Define section ranges

A section starts after its heading and ends immediately before the next heading at the same or shallower level, or at end of file. A section is a leaf when no deeper heading occurs in that range.

### RS-SEC-SCID || Define section identifiers

- An H1-H5 section ID MUST contain exactly one to five hyphen-separated segments respectively.
- Segment 1 MUST match `^[A-Z]{2}$`.
- Segment 2 MUST match `^[A-Z]{3}$` when present.
- Segments 3-5 MUST match `^[A-Z0-9]{4,5}$` when present.
- Every non-H1 ID MUST start with its parent ID followed by `-`.
- Section IDs MUST be unique within a document.

### RS-SEC-TITL || Define section titles

A section title MUST be non-empty ASCII, MUST NOT contain `||`, and MUST NOT contain an all-caps RFC 2119 keyword.

## RS-LVL || Define section roles

- H1-H2 sections are structural.
- An H3-H5 section is a requirement group exactly when it is a leaf and directly scopes one or more Requirements Tables.
- A structural section MUST NOT directly scope a Requirements Table.
- A requirement group MUST NOT directly scope a Registry Table.

## RS-TBL || Define pipe tables

### RS-TBL-PIPE || Parse and escape pipe tables

A pipe table has a header row, delimiter row, and zero or more data rows. Unescaped `|` characters delimit cells. A literal pipe inside a cell MUST be written as `\|`; validators MUST treat that pair as cell content rather than a delimiter and expose the decoded cell value as `|`. A literal backslash not preceding `|` remains a backslash. Every row MUST have the same number of cells after trimming optional leading and trailing delimiters.

Content whose exact representation would become ambiguous under this grammar SHOULD use fenced code or prose instead of a table cell.

### RS-TBL-REQT || Define requirements tables

A Requirements Table has exactly these four decoded and trimmed header cells in this order:

`REQUIREMENT_ID | REQUIREMENT_TEXT | IMPLEMENTATION_NOTES | DERIVED_FROM`

Every data row MUST contain exactly four cells.

### RS-TBL-REGT || Define registry tables

A Registry Table is any pipe table that is not a Requirements Table. Its header MUST NOT contain a reserved Requirements Table column. Registry-table cell text MUST NOT contain an all-caps RFC 2119 keyword.

## RS-REQ || Define project requirements

### RS-REQ-RQID || Define requirement identifiers

- Each Requirement Item MUST have a non-empty ID equal to `<requirement_group_section_id>-<item_code>`.
- `item_code` MUST match `^[A-Z]{2,5}_[A-Z0-9_]{2,20}$` and contain at least one underscore.
- Requirement IDs MUST be unique within a document.
- Valid existing materialized project IDs SHOULD be preserved.

### RS-REQ-CLAU || Define requirement clauses

Requirement text MUST be non-empty and contain at least one all-caps RFC 2119 keyword. A project Requirement Item MUST express a genuine project constraint, not a framework authoring or validator rule.

### RS-REQ-ORIG || Define Product Spec ancestry

Every Product Spec Requirement Item MUST use exactly `(origin)` in `DERIVED_FROM`. Product Brief and other non-normative source documents MUST NOT appear in normative ancestry.

### RS-REQ-DERV || Define derived-document ancestry

- Every Derived Document Requirement Item MUST contain a comma-separated list of one or more immediate upstream project requirement IDs.
- The list MUST contain no whitespace and MUST NOT use `(origin)` or `(none)`.
- Every reference MUST exist in a document connected by an inbound `normative` edge in the canonical index edge registry.
- Same-document derivation and transitive-only ancestry MUST NOT be stored.
- Normative ancestry MUST be acyclic.

## RS-REF || Define shared reference syntax

### RS-REF-LIST || Define values, lists, and absence

Typed scalar fields contain one value. Typed list fields contain comma-separated values with no whitespace. `(none)` is the canonical absence sentinel for any typed field whose binding declares `empty: allowed` or `EMPTY_SENTINEL=allowed`.

For a scalar field, `(none)` means no scalar value. For a list field, `(none)` means an empty set. A field whose empty policy is `forbidden` MUST NOT use `(none)`. `(none)` MUST appear alone and MUST NOT be mixed with concrete values.

### RS-REF-PATH || Define repository-relative paths

Repository-relative paths use `/`, contain no drive or leading slash, and MUST NOT contain `./`, `../`, duplicate separators, or `\`. `source_docs` paths are non-normative provenance and never create normative ancestry. Materialized `document_path` values MUST be concrete and MUST NOT contain `<NNN>` or another class pattern.

### RS-REF-GLBX || Define path-glob syntax

`path_glob` matching is case-sensitive and uses `/` as the only separator independent of host OS. Literal characters match themselves. Within one path segment, `*` matches zero or more characters, `?` matches exactly one character, and a positive character class such as `[abc]` or `[a-z]` matches one listed character; those constructs MUST NOT match `/`. `**` is recursive only when it is a complete path segment and matches zero or more complete path segments. Negated character classes, brace expansion, and other undeclared glob extensions are unsupported. Malformed supported syntax is invalid. Validators MUST distinguish invalid syntax, unsupported syntax, demonstrated non-containment, and containment that cannot be established; unestablished containment MUST NOT be accepted.

### RS-REF-TYPD || Resolve typed references

Each typed-reference field MUST resolve against its declared target namespace. Global bindings are declared by the index; document-local bindings are declared by the owning template. Every target namespace MUST exist in the index namespace registry. A value in one namespace MUST NOT be accepted because identical text exists in another namespace.

## RS-TMP || Define template placeholders

### RS-TMP-PLCH || Constrain placeholders

- Template prose and registry cells MAY contain placeholders matching `{{[a-z][a-z0-9_]*}}`.
- A template-mode Requirement Item MAY contain such placeholders in `REQUIREMENT_TEXT`, `IMPLEMENTATION_NOTES`, or `DERIVED_FROM` only when its syntactically valid `item_code` contains `TBD`.
- A placeholder Requirement Item MUST still satisfy Requirement ID grammar and its Requirement Text MUST contain an RFC 2119 keyword.
- Materialization MUST replace or remove every placeholder Requirement Item; placeholder rows are scaffolding and MUST NOT survive as project requirements.
- Materialized project content MUST NOT contain an unresolved `{{placeholder_name}}`, a Requirement Item whose item code contains `TBD`, the whole word `TBD` outside fenced code, or a document-class path pattern such as `<NNN>`.

## RS-VAL || Define deterministic validity

Project-set deterministic validity requires structural validity, canonical-index mirror agreement, project-slug consistency, graph completeness, declared deterministic readiness, applicability conformance, correct typed references, valid table escaping, and absence of cross-project references.
