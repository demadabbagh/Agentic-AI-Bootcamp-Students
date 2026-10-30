import { jsPDF } from 'jspdf'
import { formatDate, formatMoney } from './itineraryAdapter.js'

const TEAL = [36, 92, 106]
const MUTED = [110, 130, 140]
const AMBER = [160, 95, 20]
const RED = [170, 40, 40]
const PAGE = { width: 210, height: 297, margin: 16 }

// jsPDF's built-in Helvetica covers Latin-1 only: map common typography and
// strip accents it cannot draw, so names never turn into garbage glyphs.
const REPLACEMENTS = { '–': '-', '—': '-', '‘': "'", '’': "'", '“': '"', '”': '"', '…': '...', '•': '-', '€': 'EUR ', '·': '-' }
export function pdfText(value) {
  return Array.from(String(value ?? ''))
    .map((ch) => {
      if (REPLACEMENTS[ch]) return REPLACEMENTS[ch]
      if (ch.charCodeAt(0) <= 255) return ch
      const plain = ch.normalize('NFKD').replace(/[̀-ͯ]/g, '')
      return plain && plain.charCodeAt(0) <= 255 ? plain : '?'
    })
    .join('')
}

export function pdfFileName(destination, startDate) {
  const slug = String(destination || 'trip')
    .normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'trip'
  return `rihla-${slug}${startDate ? `-${startDate}` : ''}.pdf`
}

// trip: { view (buildTripView or SAMPLE_TRIP shape), verificationLabel, verificationDetail,
//         decisionLabel, warnings: string[], sampleLabel, generatedAt }
export function buildTripPdf(trip) {
  const { view } = trip
  const doc = new jsPDF({ unit: 'mm', format: 'a4' })
  const width = PAGE.width - PAGE.margin * 2
  let y = PAGE.margin

  const ensure = (height) => {
    if (y + height > PAGE.height - PAGE.margin - 8) {
      doc.addPage()
      y = PAGE.margin
    }
  }
  const write = (text, { size = 10, color = [28, 53, 68], style = 'normal', indent = 0, gap = 1.5 } = {}) => {
    doc.setFont('helvetica', style)
    doc.setFontSize(size)
    doc.setTextColor(...color)
    const lines = doc.splitTextToSize(pdfText(text), width - indent)
    const lineHeight = size * 0.42
    lines.forEach((line) => {
      ensure(lineHeight)
      doc.text(line, PAGE.margin + indent, y + lineHeight * 0.8)
      y += lineHeight
    })
    y += gap
  }
  const rule = () => {
    ensure(4)
    doc.setDrawColor(220, 232, 235)
    doc.line(PAGE.margin, y, PAGE.margin + width, y)
    y += 4
  }
  const money = (value) => formatMoney(value, view.currency)

  // Branding
  write('Rihla.', { size: 22, style: 'bold', color: TEAL, gap: 0 })
  write('AI trip itinerary', { size: 10, color: MUTED, gap: 4 })

  if (trip.sampleLabel) {
    // Measure with the font the banner is drawn in, or the text runs past the box.
    doc.setFont('helvetica', 'bold')
    doc.setFontSize(11)
    const lines = doc.splitTextToSize(pdfText(`SAMPLE TRIP - ${trip.sampleLabel}`), width - 8)
    const boxHeight = lines.length * 4.6 + 5
    ensure(boxHeight)
    doc.setFillColor(255, 236, 236)
    doc.setDrawColor(...RED)
    doc.rect(PAGE.margin, y, width, boxHeight, 'FD')
    doc.setTextColor(...RED)
    lines.forEach((line, i) => doc.text(line, PAGE.margin + 4, y + 5.5 + i * 4.6))
    y += boxHeight + 4
  }

  write(view.destination || 'Destination not specified', { size: 18, style: 'bold', gap: 2 })
  const dates = view.startDate
    ? `${formatDate(view.startDate)} - ${formatDate(view.endDate)}${/^\d{4}/.test(view.startDate) ? `, ${view.startDate.slice(0, 4)}` : ''}`
    : 'Dates not specified'
  const travellers = view.travellers ?? 'Not specified'
  const estimate = view.estimatedTotal === null || view.estimatedTotal === undefined
    ? 'Unknown'
    : `${money(view.estimatedTotal)}${view.unknownCostCount > 0 ? ` + ${view.unknownCostCount} unknown price(s)` : ''}`
  ;[
    ['Travel dates', dates],
    ['Travellers', String(travellers)],
    ['Budget', view.budgetTotal === null || view.budgetTotal === undefined ? 'Not specified' : `${money(view.budgetTotal)} (${view.currency || 'USD'})`],
    ['Estimated planned costs', estimate],
    ['AI verification', trip.verificationLabel || 'Not verified'],
    ['Your decision', trip.decisionLabel || 'Not decided'],
  ].forEach(([label, value]) => write(`${label}: ${value}`, { size: 10, gap: 0.8 }))
  if (trip.verificationDetail) write(trip.verificationDetail, { size: 9, color: MUTED, gap: 2 })

  const warnings = (trip.warnings || []).filter(Boolean)
  if (warnings.length) {
    rule()
    write('Warnings and limitations', { size: 12, style: 'bold', color: AMBER, gap: 1 })
    warnings.forEach((w) => write(`- ${w}`, { size: 9, color: AMBER, indent: 2, gap: 1 }))
  }

  ;(view.days || []).forEach((day) => {
    rule()
    ensure(16)
    write(`${day.label}${day.date ? ` - ${day.date}` : ''}`, { size: 13, style: 'bold', color: TEAL, gap: 1 })
    const activities = day.activities || []
    if (!activities.length) {
      write('Nothing is planned for this day.', { size: 9, color: MUTED })
      return
    }
    if (!trip.sampleLabel && !activities.some((a) => a.travel)) {
      write('Transportation information unavailable: travel times between stops were not provided.', { size: 8.5, color: MUTED })
    }
    activities.forEach((a) => {
      ensure(14)
      write(`${a.time || 'Flexible time'}   ${a.name || 'Unnamed activity'}`, { size: 10.5, style: 'bold', gap: 0.5 })
      const price = a.price || 'Price unknown'
      const details = [a.type, a.duration, price].filter(Boolean).join('  |  ')
      write(details, { size: 9, color: price === 'Price unknown' ? AMBER : MUTED, indent: 4, gap: 0.5 })
      if (a.notes) write(a.notes, { size: 8.5, color: MUTED, indent: 4, gap: 0.5 })
      if (a.travel) write(a.travel, { size: 8.5, color: MUTED, indent: 4, gap: 0.5 })
      if (a.sourceUrl) write(`Source: ${a.sourceUrl}`, { size: 7.5, color: MUTED, indent: 4, gap: 0.5 })
      y += 1.5
    })
  })

  const generated = (trip.generatedAt || new Date()).toISOString().slice(0, 10)
  const pages = doc.getNumberOfPages()
  for (let page = 1; page <= pages; page += 1) {
    doc.setPage(page)
    doc.setFont('helvetica', 'normal')
    doc.setFontSize(7.5)
    doc.setTextColor(...MUTED)
    doc.text(
      pdfText(`Rihla - exported ${generated}. Prices are estimates from the listed sources; unknown prices are not counted as free.`),
      PAGE.margin,
      PAGE.height - 8
    )
    doc.text(`Page ${page} of ${pages}`, PAGE.width - PAGE.margin, PAGE.height - 8, { align: 'right' })
  }
  return doc
}

export function downloadTripPdf(trip) {
  const doc = buildTripPdf(trip)
  doc.save(pdfFileName(trip.view.destination, trip.view.startDate))
}
