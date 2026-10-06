import { useRef, useState, type FormEvent } from 'react'
import { ArrowRight, FilePlus2, FileText, LoaderCircle, Search, Upload } from 'lucide-react'
import type { KnowledgeDocument, SearchResult } from '../../types'

interface Props {
  results: SearchResult[] | null
  searching: boolean
  saving: boolean
  search: (query: string) => Promise<void>
  mutate: (input: KnowledgeDocument | File) => Promise<boolean>
}

export function KnowledgeTools({ results, searching, saving, search, mutate }: Props) {
  const [query, setQuery] = useState('')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [fileError, setFileError] = useState('')
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  async function add(event: FormEvent) {
    event.preventDefault()
    if (!title.trim() || !content.trim() || saving) return
    if (await mutate({ title: title.trim(), content: content.trim() })) {
      setTitle('')
      setContent('')
    }
  }
  async function upload(file?: File) {
    if (!file || saving) return
    if (!/\.(txt|md|json)$/i.test(file.name)) {
      setFileError('Choose a .txt, .md, or .json file.')
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      setFileError('Choose a file smaller than 5 MB.')
      return
    }
    if (!file.size) {
      setFileError('This file is empty. Choose a file with content.')
      return
    }
    setFileError('')
    await mutate(file)
  }
  return (
    <div className="knowledge-grid">
      <section className="admin-card knowledge-search-card">
        <div className="card-heading">
          <div className="card-icon">
            <Search size={19} />
          </div>
          <div>
            <span className="eyebrow">FIND THE SIGNAL</span>
            <h2>Explore your knowledge.</h2>
          </div>
          <span className="label-tag">RAG</span>
        </div>
        <p className="card-description">See the source material your support agents can draw from.</p>
        <form
          className="knowledge-search-form"
          onSubmit={(event) => {
            event.preventDefault()
            void search(query)
          }}
        >
          <label className="search-input">
            <Search size={16} />
            <input
              aria-label="Search support knowledge"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="e.g. How long does a refund take?"
            />
          </label>
          <button
            className="button primary"
            type="submit"
            disabled={searching || !query.trim()}
            aria-label="Search knowledge"
          >
            {searching ? <LoaderCircle size={17} className="spin" /> : <ArrowRight size={17} />}
          </button>
        </form>
        <div aria-live="polite" aria-busy={searching}>
          {searching ? (
            <div className="search-skeleton">
              <span />
              <span />
              <span />
            </div>
          ) : results === null ? (
            <div className="empty-state">
              <div className="empty-illustration">
                <FileText size={30} strokeWidth={1.2} />
                <Search size={20} strokeWidth={1.5} />
              </div>
              <h3>The right answer starts here.</h3>
              <p>
                Search a topic to explore relevant policies,
                <br />
                documents, and support guidance.
              </p>
            </div>
          ) : results.length === 0 ? (
            <div className="empty-state">
              <Search size={27} />
              <h3>No matches yet.</h3>
              <p>Try a broader phrase or add a document to your knowledge base.</p>
            </div>
          ) : (
            <div className="search-results">
              <p className="result-count">
                {results.length} relevant {results.length === 1 ? 'document' : 'documents'}
              </p>
              {results.map((result, index) => (
                <article key={`${result.id}-${index}`}>
                  <header>
                    <span className="result-index">{String(index + 1).padStart(2, '0')}</span>
                    <h3>{result.title}</h3>
                    {result.score !== null && (
                      <span
                        className="score-badge"
                        title="Backend relevance score; not a confidence percentage"
                      >
                        Score {result.score.toFixed(3)}
                      </span>
                    )}
                  </header>
                  <p>{result.content}</p>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>
      <section className="admin-card">
        <div className="card-heading">
          <div className="card-icon peach">
            <FilePlus2 size={19} />
          </div>
          <div>
            <span className="eyebrow">BUILD ON WHAT YOU KNOW</span>
            <h2>Give your team context.</h2>
          </div>
        </div>
        <p className="card-description">Turn useful information into better support.</p>
        <form onSubmit={(event) => void add(event)}>
          <label className="field">
            <span>Document title</span>
            <input
              placeholder="e.g. Returns & refund policy"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              required
              disabled={saving}
            />
          </label>
          <label className="field">
            <span>Content</span>
            <textarea
              rows={5}
              placeholder="Add the details your support team should know…"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              required
              disabled={saving}
            />
          </label>
          <button
            className="button primary"
            type="submit"
            disabled={saving || !title.trim() || !content.trim()}
          >
            {saving ? <LoaderCircle size={15} className="spin" /> : <FilePlus2 size={15} />}
            {saving ? 'Saving…' : 'Add document'}
          </button>
        </form>
        <div
          className={`upload-zone ${dragging ? 'dragging' : ''}`}
          onDragOver={(event) => {
            event.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setDragging(false)
            void upload(event.dataTransfer.files[0])
          }}
        >
          <Upload size={21} strokeWidth={1.5} />
          <div>
            <button type="button" disabled={saving} onClick={() => inputRef.current?.click()}>
              Choose a file
            </button>
            <span> or drop it here</span>
            <p>TXT, Markdown, or JSON · up to 5 MB</p>
          </div>
          <input
            ref={inputRef}
            className="visually-hidden"
            type="file"
            accept=".txt,.md,.json"
            aria-label="Upload knowledge file"
            disabled={saving}
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              void upload(file)
            }}
          />
        </div>
        {fileError && (
          <div className="inline-error" role="alert">
            {fileError}
          </div>
        )}
      </section>
    </div>
  )
}
