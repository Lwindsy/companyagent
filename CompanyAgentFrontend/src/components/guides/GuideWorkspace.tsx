import { useMemo, useState } from 'react'
import { ArrowLeft, ArrowUpRight, BookOpen, ChevronRight, Search, Sparkles } from 'lucide-react'
import { customerDocuments } from '../../content/customerDocuments'
import { SupportOrb } from '../ui/Brand'

export function GuideWorkspace({
  selectedId,
  select,
  back,
}: {
  selectedId: string
  select: (id: string) => void
  back: () => void
}) {
  const [query, setQuery] = useState('')
  const selected = customerDocuments.find((document) => document.id === selectedId) || customerDocuments[0]
  const sections = useMemo(
    () =>
      selected.sections.filter((section) =>
        `${section.title} ${section.detail} ${section.items.map((item) => `${item.title} ${item.detail}`).join(' ')}`
          .toLowerCase()
          .includes(query.toLowerCase()),
      ),
    [selected, query],
  )
  return (
    <div className="guides-workspace">
      <header className="guides-hero">
        <div>
          <span className="welcome-eyebrow">
            <Sparkles size={14} /> UNDERSTANDING, BY DESIGN
          </span>
          <h1>
            A little knowledge.
            <br />
            <span>A clearer picture.</span>
          </h1>
          <p>
            Get to know the thinking, people, and technology
            <br className="desktop-break" /> behind every next step.
          </p>
        </div>
        <SupportOrb />
      </header>
      <div className="guide-tabs" role="tablist" aria-label="Guide categories">
        {customerDocuments.map((document) => (
          <button
            key={document.id}
            role="tab"
            aria-selected={selected.id === document.id}
            aria-controls="guide-content"
            id={`tab-${document.id}`}
            onClick={() => {
              select(document.id)
              setQuery('')
            }}
            className={selected.id === document.id ? 'active' : ''}
          >
            <BookOpen size={16} />
            {document.label}
            <span>{document.sections.length} chapters</span>
          </button>
        ))}
      </div>
      <div className="guide-layout">
        <aside className="guide-toc">
          <label className="search-input">
            <Search size={15} />
            <input
              aria-label="Search this guide"
              placeholder="Find in this guide…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <span className="eyebrow">IN THIS GUIDE</span>
          <nav aria-label="Table of contents">
            {sections.map((section) => (
              <a key={section.number} href={`#chapter-${selected.id}-${section.number}`}>
                <span>{section.number.padStart(2, '0')}</span>
                {section.title}
                <ChevronRight size={12} />
              </a>
            ))}
          </nav>
          <div className="toc-help">
            <Sparkles size={18} />
            <h3>Still have a question?</h3>
            <p>Let’s turn it into a conversation.</p>
            <button onClick={back}>
              Ask support <ArrowUpRight size={15} />
            </button>
          </div>
        </aside>
        <article
          className="guide-content"
          id="guide-content"
          role="tabpanel"
          aria-labelledby={`tab-${selected.id}`}
        >
          <header className="guide-intro">
            <span className="eyebrow">{selected.eyebrow.toUpperCase()}</span>
            <h2>{selected.title}</h2>
            <p>{selected.intro}</p>
            <div className="reading-meta">
              <span>{selected.sections.length} chapters</span>
              <span>
                About {Math.max(3, Math.ceil(JSON.stringify(selected).split(' ').length / 200))} min read
              </span>
            </div>
          </header>
          {sections.length === 0 && (
            <div className="empty-state">
              <Search size={26} />
              <h3>No chapters found.</h3>
              <p>Try another phrase, or clear the search to explore the whole guide.</p>
              <button className="button secondary" onClick={() => setQuery('')}>
                Clear search
              </button>
            </div>
          )}
          {sections.map((section) => (
            <section
              className="guide-chapter"
              key={section.number}
              id={`chapter-${selected.id}-${section.number}`}
            >
              <div className="chapter-heading">
                <span>{section.number.padStart(2, '0')}</span>
                <h3>{section.title}</h3>
              </div>
              <p className="chapter-detail">{section.detail}</p>
              <div className="chapter-items">
                {section.items.map((item) => (
                  <div className="chapter-item" key={item.title}>
                    <h4>{item.title.replace(/^\d+\.\d+\s*/, '')}</h4>
                    <p>{item.detail}</p>
                  </div>
                ))}
              </div>
            </section>
          ))}
          <button className="button secondary" onClick={back}>
            <ArrowLeft size={15} /> Back to your conversation
          </button>
        </article>
      </div>
    </div>
  )
}
