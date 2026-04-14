// NeoDemos GrapeJS component library
// Registers custom component types with traits, containment rules, and structural locking.
// Uses the site's actual CSS classes (see static/css/components.css and static/css/pages/*.css).
//
// Loaded directly via <script src="/static/admin-editor/components.js"> — plain browser JS,
// no modules, no bundling. Exposes window.registerNDComponents(editor) to be called after
// grapesjs.init() in templates/admin/editor.html.

(function (window) {
  'use strict';

  // Helpers ------------------------------------------------------------------

  // Find the first descendant with a given class, or undefined.
  function findByClass(model, cls) {
    if (!model || !model.find) return undefined;
    const res = model.find('.' + cls);
    return res && res[0] ? res[0] : undefined;
  }

  // Safely set inner text of a component.
  function setText(model, txt) {
    if (model && typeof model.components === 'function') {
      model.components(txt == null ? '' : String(txt));
    }
  }

  // Swap one class for another on a component's classes collection.
  function swapClass(model, oldCls, newCls) {
    if (!model) return;
    if (oldCls) model.removeClass(oldCls);
    if (newCls) model.addClass(newCls);
  }

  // Register all NeoDemos component types --------------------------------

  function registerNDComponents(editor) {
    const dc = editor.DomComponents;

    // =========================================================================
    // Pattern 1: Hero (wrapper + locked overlay + locked title/subtitle)
    // =========================================================================

    dc.addType('nd-subpage-hero', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('subpage-hero-image'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['subpage-hero-image'],
          style: {
            'background-image': "url('/static/images/erasmusbrug.jpg')",
          },
          attributes: {},
          traits: [
            { type: 'text', name: 'image_url', label: 'Afbeelding URL', placeholder: '/static/images/...' },
            { type: 'text', name: 'title', label: 'Titel' },
            { type: 'text', name: 'subtitle', label: 'Ondertitel' },
          ],
          image_url: '/static/images/erasmusbrug.jpg',
          title: 'Titel hier',
          subtitle: 'Ondertitel hier',
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          propagate: ['stylable'],
          components: [
            { type: 'nd-subpage-hero-overlay' },
          ],
        },
        init() {
          this.on('change:image_url', this.onImageChange);
          this.on('change:title', this.onTitleChange);
          this.on('change:subtitle', this.onSubtitleChange);
        },
        onImageChange() {
          const url = this.get('image_url') || '/static/images/erasmusbrug.jpg';
          this.setStyle(Object.assign({}, this.getStyle(), {
            'background-image': "url('" + url + "')",
          }));
        },
        onTitleChange() {
          const el = findByClass(this, 'subpage-hero-title');
          if (el) setText(el, this.get('title') || '');
        },
        onSubtitleChange() {
          const el = findByClass(this, 'subpage-hero-subtitle');
          if (el) setText(el, this.get('subtitle') || '');
        },
      },
    });

    dc.addType('nd-subpage-hero-overlay', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('subpage-hero-overlay'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['subpage-hero-overlay'],
          draggable: false,
          removable: false,
          copyable: false,
          selectable: false,
          stylable: false,
          components: [
            { type: 'nd-subpage-hero-title' },
            { type: 'nd-subpage-hero-subtitle' },
          ],
        },
      },
    });

    dc.addType('nd-subpage-hero-title', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('subpage-hero-title'));
      },
      model: {
        defaults: {
          tagName: 'h1',
          classes: ['subpage-hero-title'],
          draggable: false,
          removable: false,
          copyable: false,
          stylable: false,
          editable: false,
          components: 'Titel hier',
        },
      },
    });

    dc.addType('nd-subpage-hero-subtitle', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('subpage-hero-subtitle'));
      },
      model: {
        defaults: {
          tagName: 'p',
          classes: ['subpage-hero-subtitle'],
          draggable: false,
          removable: false,
          copyable: false,
          stylable: false,
          editable: false,
          components: 'Ondertitel hier',
        },
      },
    });

    // =========================================================================
    // Pattern 2: Section with variant (default / highlight / testimonial)
    // =========================================================================

    const SECTION_VARIANT_CLASS = {
      default: '',
      highlight: 'subpage-section--highlight',
      testimonial: 'subpage-section--testimonial',
    };

    dc.addType('nd-section', {
      isComponent: function (el) {
        if (!el.classList) return false;
        // Match only bare subpage-section (not CTA, which is its own type).
        if (!el.classList.contains('subpage-section')) return false;
        if (el.classList.contains('subpage-section--cta')) return false;
        return true;
      },
      model: {
        defaults: {
          tagName: 'section',
          classes: ['subpage-section'],
          traits: [
            {
              type: 'select',
              name: 'variant',
              label: 'Variant',
              options: [
                { value: 'default', name: 'Standaard' },
                { value: 'highlight', name: 'Highlight' },
                { value: 'testimonial', name: 'Testimonial' },
              ],
            },
          ],
          variant: 'default',
          draggable: true,
          droppable: true,
          removable: true,
          copyable: true,
          stylable: false,
          components: [
            { type: 'nd-heading-h2', components: 'Kop van de sectie' },
            { type: 'nd-paragraph', components: 'Introductietekst of paragraaf voor deze sectie.' },
          ],
        },
        init() {
          this.on('change:variant', this.onVariantChange);
        },
        onVariantChange() {
          const prev = this.previous('variant');
          const next = this.get('variant') || 'default';
          const prevCls = SECTION_VARIANT_CLASS[prev] || '';
          const nextCls = SECTION_VARIANT_CLASS[next] || '';
          swapClass(this, prevCls, nextCls);
        },
      },
    });

    // =========================================================================
    // Pattern 3: Founder quote (locked internal structure)
    // =========================================================================

    dc.addType('nd-founder-quote', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('founder-quote'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['founder-quote'],
          traits: [
            { type: 'textarea', name: 'quote', label: 'Citaat' },
            { type: 'text', name: 'cite', label: 'Bron' },
          ],
          quote: '\u201cGroot citaat dat de kern van de boodschap weergeeft.\u201d',
          cite: '\u2014 Dennis Tak, oud-raadslid Rotterdam',
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          propagate: ['stylable'],
          components: [
            {
              type: 'nd-founder-quote-inner',
            },
          ],
        },
        init() {
          this.on('change:quote', this.onQuoteChange);
          this.on('change:cite', this.onCiteChange);
        },
        onQuoteChange() {
          const p = findByClass(this, 'founder-quote-p') || (this.find('blockquote p') || [])[0];
          if (p) setText(p, this.get('quote') || '');
        },
        onCiteChange() {
          const c = (this.find('blockquote cite') || [])[0];
          if (c) setText(c, this.get('cite') || '');
        },
      },
    });

    dc.addType('nd-founder-quote-inner', {
      model: {
        defaults: {
          tagName: 'blockquote',
          draggable: false,
          removable: false,
          copyable: false,
          selectable: false,
          stylable: false,
          components: [
            {
              tagName: 'p',
              classes: ['founder-quote-p'],
              type: 'text',
              draggable: false,
              removable: false,
              copyable: false,
              selectable: false,
              editable: false,
              stylable: false,
              components: '\u201cGroot citaat dat de kern van de boodschap weergeeft.\u201d',
            },
            {
              tagName: 'cite',
              type: 'text',
              draggable: false,
              removable: false,
              copyable: false,
              selectable: false,
              editable: false,
              stylable: false,
              components: '\u2014 Dennis Tak, oud-raadslid Rotterdam',
            },
          ],
        },
      },
    });

    // =========================================================================
    // Pattern 4: Grid + Card pairs
    // =========================================================================

    // --- nd-audience-grid / nd-audience-card ---

    dc.addType('nd-audience-grid', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('audience-grid'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['audience-grid'],
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          droppable: '[data-gjs-type=nd-audience-card]',
          components: [
            { type: 'nd-audience-card', title: 'Raadsleden & fractiemedewerkers', text: 'Snellere dossiervoorbereiding, inzicht in stemgedrag en beleidscontinu\u00efteit.' },
            { type: 'nd-audience-card', title: 'Journalisten', text: 'Feitelijke basis voor onderzoeksverhalen, met directe verwijzing naar brondocumenten.' },
            { type: 'nd-audience-card', title: 'Ambtenaren', text: 'Overzicht van eerdere besluiten en toezeggingen bij beleidsvoorbereiding.' },
            { type: 'nd-audience-card', title: 'Betrokken bewoners', text: 'Begrijpelijke antwoorden op vragen over wat de gemeente heeft gedaan en besloten.' },
          ],
        },
      },
    });

    dc.addType('nd-audience-card', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('audience-card'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['audience-card'],
          traits: [
            { type: 'text', name: 'title', label: 'Titel' },
            { type: 'textarea', name: 'text', label: 'Tekst' },
          ],
          title: 'Kaarttitel',
          text: 'Kaarttekst.',
          draggable: '[data-gjs-type=nd-audience-grid]',
          removable: true,
          copyable: true,
          stylable: false,
          components: [
            { tagName: 'h3', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Kaarttitel' },
            { tagName: 'p', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Kaarttekst.' },
          ],
        },
        init() {
          this.on('change:title', this.onTitleChange);
          this.on('change:text', this.onTextChange);
          // Seed initial render from attrs if provided via component def.
          const self = this;
          setTimeout(function () { self.onTitleChange(); self.onTextChange(); }, 0);
        },
        onTitleChange() {
          const el = (this.find('h3') || [])[0];
          if (el) setText(el, this.get('title') || '');
        },
        onTextChange() {
          const el = (this.find('p') || [])[0];
          if (el) setText(el, this.get('text') || '');
        },
      },
    });

    // --- nd-sovereignty-grid / nd-sovereignty-card ---

    dc.addType('nd-sovereignty-grid', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('sovereignty-grid'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['sovereignty-grid'],
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          droppable: '[data-gjs-type=nd-sovereignty-card]',
          components: [
            { type: 'nd-sovereignty-card', title: 'Hetzner, Duitsland', text: 'Servers in Duitse datacenters. Europese jurisdictie, GDPR-compliant.' },
            { type: 'nd-sovereignty-card', title: 'Nebius, EU AI', text: 'AI-inferentie via Europese providers. Geen data naar OpenAI of Google servers.' },
            { type: 'nd-sovereignty-card', title: 'Geen US CLOUD Act', text: 'Geen Amerikaanse partij kan uw gegevens opvragen via juridische achterdeur.' },
          ],
        },
      },
    });

    dc.addType('nd-sovereignty-card', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('sovereignty-card'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['sovereignty-card'],
          traits: [
            { type: 'text', name: 'title', label: 'Titel' },
            { type: 'textarea', name: 'text', label: 'Tekst' },
          ],
          title: 'Kaarttitel',
          text: 'Kaarttekst.',
          draggable: '[data-gjs-type=nd-sovereignty-grid]',
          removable: true,
          copyable: true,
          stylable: false,
          components: [
            { tagName: 'h3', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Kaarttitel' },
            { tagName: 'p', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Kaarttekst.' },
          ],
        },
        init() {
          this.on('change:title', this.onTitleChange);
          this.on('change:text', this.onTextChange);
          const self = this;
          setTimeout(function () { self.onTitleChange(); self.onTextChange(); }, 0);
        },
        onTitleChange() {
          const el = (this.find('h3') || [])[0];
          if (el) setText(el, this.get('title') || '');
        },
        onTextChange() {
          const el = (this.find('p') || [])[0];
          if (el) setText(el, this.get('text') || '');
        },
      },
    });

    // --- nd-stat-grid / nd-stat-card (CSS class: source-stats / stat-card) ---

    dc.addType('nd-stat-grid', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('source-stats'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['source-stats'],
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          droppable: '[data-gjs-type=nd-stat-card]',
          components: [
            { type: 'nd-stat-card', number: '90.000+', label: 'documenten' },
            { type: 'nd-stat-card', number: '2002\u2013heden', label: 'periode' },
            { type: 'nd-stat-card', number: '24 jaar', label: 'raadsgeschiedenis' },
          ],
        },
      },
    });

    dc.addType('nd-stat-card', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('stat-card'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['stat-card'],
          traits: [
            { type: 'text', name: 'number', label: 'Getal' },
            { type: 'text', name: 'label', label: 'Label' },
          ],
          number: '0',
          label: 'label',
          draggable: '[data-gjs-type=nd-stat-grid]',
          removable: true,
          copyable: true,
          stylable: false,
          components: [
            { tagName: 'span', classes: ['stat-number'], type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: '0' },
            { tagName: 'span', classes: ['stat-label'], type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'label' },
          ],
        },
        init() {
          this.on('change:number', this.onNumberChange);
          this.on('change:label', this.onLabelChange);
          const self = this;
          setTimeout(function () { self.onNumberChange(); self.onLabelChange(); }, 0);
        },
        onNumberChange() {
          const el = findByClass(this, 'stat-number');
          if (el) setText(el, this.get('number') || '');
        },
        onLabelChange() {
          const el = findByClass(this, 'stat-label');
          if (el) setText(el, this.get('label') || '');
        },
      },
    });

    // --- nd-eval-grid / nd-eval-card ---

    dc.addType('nd-eval-grid', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('eval-grid'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['eval-grid'],
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          droppable: '[data-gjs-type=nd-eval-card]',
          components: [
            { type: 'nd-eval-card', score: '0.99', label: 'Precisie', description: 'Verwijst het antwoord naar de juiste brondocumenten?' },
            { type: 'nd-eval-card', score: '4.8 / 5', label: 'Getrouwheid', description: 'Komt het antwoord overeen met wat er in de bronnen staat?' },
            { type: 'nd-eval-card', score: '2.75 / 5', label: 'Volledigheid', description: 'Dekt het antwoord alle relevante aspecten van de vraag?' },
          ],
        },
      },
    });

    dc.addType('nd-eval-card', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('eval-card'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['eval-card'],
          traits: [
            { type: 'text', name: 'score', label: 'Score' },
            { type: 'text', name: 'label', label: 'Label' },
            { type: 'textarea', name: 'description', label: 'Beschrijving' },
          ],
          score: '0.0',
          label: 'Metriek',
          description: 'Beschrijving van de metriek.',
          draggable: '[data-gjs-type=nd-eval-grid]',
          removable: true,
          copyable: true,
          stylable: false,
          components: [
            { tagName: 'span', classes: ['eval-score'], type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: '0.0' },
            { tagName: 'span', classes: ['eval-label'], type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Metriek' },
            { tagName: 'p', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Beschrijving van de metriek.' },
          ],
        },
        init() {
          this.on('change:score', this.onScoreChange);
          this.on('change:label', this.onLabelChange);
          this.on('change:description', this.onDescChange);
          const self = this;
          setTimeout(function () {
            self.onScoreChange();
            self.onLabelChange();
            self.onDescChange();
          }, 0);
        },
        onScoreChange() {
          const el = findByClass(this, 'eval-score');
          if (el) setText(el, this.get('score') || '');
        },
        onLabelChange() {
          const el = findByClass(this, 'eval-label');
          if (el) setText(el, this.get('label') || '');
        },
        onDescChange() {
          const el = (this.find('p') || [])[0];
          if (el) setText(el, this.get('description') || '');
        },
      },
    });

    // =========================================================================
    // Pattern 5: Checklist (variant select)
    // =========================================================================

    const CHECKLIST_VARIANT_CLASS = {
      security: 'security-checklist',
      transparency: 'transparency-list',
      limitations: 'limitations-list',
    };

    dc.addType('nd-checklist', {
      isComponent: function (el) {
        if (!el.classList) return false;
        return (
          el.classList.contains('security-checklist') ||
          el.classList.contains('transparency-list') ||
          el.classList.contains('limitations-list')
        );
      },
      model: {
        defaults: {
          tagName: 'ul',
          classes: ['security-checklist'],
          traits: [
            {
              type: 'select',
              name: 'variant',
              label: 'Variant',
              options: [
                { value: 'security', name: 'Security checklist' },
                { value: 'transparency', name: 'Transparantie lijst' },
                { value: 'limitations', name: 'Beperkingen lijst' },
              ],
            },
          ],
          variant: 'security',
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          droppable: 'li',
          components: [
            { tagName: 'li', type: 'text', editable: true, draggable: true, removable: true, stylable: false, components: 'Eerste punt op de lijst' },
            { tagName: 'li', type: 'text', editable: true, draggable: true, removable: true, stylable: false, components: 'Tweede punt op de lijst' },
            { tagName: 'li', type: 'text', editable: true, draggable: true, removable: true, stylable: false, components: 'Derde punt op de lijst' },
          ],
        },
        init() {
          this.on('change:variant', this.onVariantChange);
        },
        onVariantChange() {
          const prev = this.previous('variant');
          const next = this.get('variant') || 'security';
          const prevCls = CHECKLIST_VARIANT_CLASS[prev] || '';
          const nextCls = CHECKLIST_VARIANT_CLASS[next] || '';
          swapClass(this, prevCls, nextCls);
        },
      },
    });

    // =========================================================================
    // Pattern 6: Steps (architecture + methodology)
    // =========================================================================

    function defineStepsType(typeName, wrapperClass, stepTypeName) {
      dc.addType(typeName, {
        isComponent: function (el) {
          return !!(el.classList && el.classList.contains(wrapperClass));
        },
        model: {
          defaults: {
            tagName: 'div',
            classes: [wrapperClass],
            draggable: true,
            removable: true,
            copyable: true,
            stylable: false,
            droppable: '[data-gjs-type=' + stepTypeName + ']',
            components: [
              { type: stepTypeName, number: '1', title: 'Stap 1', description: 'Beschrijving van de eerste stap.' },
              { type: stepTypeName, number: '2', title: 'Stap 2', description: 'Beschrijving van de tweede stap.' },
              { type: stepTypeName, number: '3', title: 'Stap 3', description: 'Beschrijving van de derde stap.' },
            ],
          },
        },
      });
    }

    function defineStepType(typeName, stepClass, parentWrapperTypeName) {
      dc.addType(typeName, {
        isComponent: function (el) {
          return !!(el.classList && el.classList.contains(stepClass));
        },
        model: {
          defaults: {
            tagName: 'div',
            classes: [stepClass],
            traits: [
              { type: 'text', name: 'number', label: 'Nummer' },
              { type: 'text', name: 'title', label: 'Titel' },
              { type: 'textarea', name: 'description', label: 'Beschrijving' },
            ],
            number: '1',
            title: 'Stap titel',
            description: 'Beschrijving van de stap.',
            draggable: '[data-gjs-type=' + parentWrapperTypeName + ']',
            removable: true,
            copyable: true,
            stylable: false,
            components: [
              { tagName: 'span', classes: ['step-number'], type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: '1' },
              {
                tagName: 'div',
                draggable: false,
                removable: false,
                copyable: false,
                selectable: false,
                stylable: false,
                components: [
                  { tagName: 'h3', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Stap titel' },
                  { tagName: 'p', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Beschrijving van de stap.' },
                ],
              },
            ],
          },
          init() {
            this.on('change:number', this.onNumberChange);
            this.on('change:title', this.onTitleChange);
            this.on('change:description', this.onDescChange);
            const self = this;
            setTimeout(function () {
              self.onNumberChange();
              self.onTitleChange();
              self.onDescChange();
            }, 0);
          },
          onNumberChange() {
            const el = findByClass(this, 'step-number');
            if (el) setText(el, this.get('number') || '');
          },
          onTitleChange() {
            const el = (this.find('h3') || [])[0];
            if (el) setText(el, this.get('title') || '');
          },
          onDescChange() {
            const el = (this.find('p') || [])[0];
            if (el) setText(el, this.get('description') || '');
          },
        },
      });
    }

    defineStepsType('nd-architecture-steps', 'architecture-steps', 'nd-architecture-step');
    defineStepType('nd-architecture-step', 'architecture-step', 'nd-architecture-steps');

    defineStepsType('nd-methodology-steps', 'methodology-steps', 'nd-methodology-step');
    defineStepType('nd-methodology-step', 'methodology-step', 'nd-methodology-steps');

    // =========================================================================
    // Pattern 7: Compatibility list + badges
    // =========================================================================

    dc.addType('nd-compatibility-list', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('compatibility-list'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['compatibility-list'],
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          droppable: '[data-gjs-type=nd-compatibility-badge]',
          components: [
            { type: 'nd-compatibility-badge', label: 'Claude' },
            { type: 'nd-compatibility-badge', label: 'ChatGPT' },
            { type: 'nd-compatibility-badge', label: 'Cursor' },
            { type: 'nd-compatibility-badge', label: 'Ollama' },
            { type: 'nd-compatibility-badge', label: 'LM Studio' },
            { type: 'nd-compatibility-badge', label: 'Open WebUI' },
          ],
        },
      },
    });

    dc.addType('nd-compatibility-badge', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('compatibility-badge'));
      },
      model: {
        defaults: {
          tagName: 'span',
          classes: ['compatibility-badge'],
          traits: [
            { type: 'text', name: 'label', label: 'Label' },
          ],
          label: 'Badge',
          draggable: '[data-gjs-type=nd-compatibility-list]',
          removable: true,
          copyable: true,
          stylable: false,
          editable: false,
          components: 'Badge',
        },
        init() {
          this.on('change:label', this.onLabelChange);
          const self = this;
          setTimeout(function () { self.onLabelChange(); }, 0);
        },
        onLabelChange() {
          setText(this, this.get('label') || '');
        },
      },
    });

    // =========================================================================
    // Pattern 8: Button
    // =========================================================================

    const BTN_VARIANT_CLASS = {
      accent: 'btn-accent',
      primary: 'btn-primary',
      secondary: 'btn-secondary',
      ghost: 'btn-ghost',
    };

    dc.addType('nd-btn', {
      isComponent: function (el) {
        if (!el.classList) return false;
        if (el.tagName !== 'A') return false;
        return el.classList.contains('btn');
      },
      model: {
        defaults: {
          tagName: 'a',
          classes: ['btn', 'btn-accent'],
          attributes: { href: '/register' },
          traits: [
            { type: 'text', name: 'label', label: 'Label' },
            { type: 'text', name: 'href', label: 'Link' },
            {
              type: 'select',
              name: 'variant',
              label: 'Variant',
              options: [
                { value: 'accent', name: 'Accent' },
                { value: 'primary', name: 'Primair' },
                { value: 'secondary', name: 'Secundair' },
                { value: 'ghost', name: 'Ghost' },
              ],
            },
          ],
          label: 'Gratis account',
          href: '/register',
          variant: 'accent',
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          editable: false,
          components: 'Gratis account',
        },
        init() {
          this.on('change:label', this.onLabelChange);
          this.on('change:href', this.onHrefChange);
          this.on('change:variant', this.onVariantChange);
          const self = this;
          setTimeout(function () { self.onLabelChange(); self.onHrefChange(); }, 0);
        },
        onLabelChange() {
          setText(this, this.get('label') || '');
        },
        onHrefChange() {
          this.addAttributes({ href: this.get('href') || '#' });
        },
        onVariantChange() {
          const prev = this.previous('variant');
          const next = this.get('variant') || 'accent';
          const prevCls = BTN_VARIANT_CLASS[prev] || '';
          const nextCls = BTN_VARIANT_CLASS[next] || '';
          swapClass(this, prevCls, nextCls);
        },
      },
    });

    // =========================================================================
    // Pattern 9: CTA section (compound)
    // =========================================================================

    dc.addType('nd-cta-section', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('subpage-section--cta'));
      },
      model: {
        defaults: {
          tagName: 'section',
          classes: ['subpage-section', 'subpage-section--cta'],
          traits: [
            { type: 'text', name: 'title', label: 'Titel' },
            { type: 'textarea', name: 'text', label: 'Tekst' },
            { type: 'text', name: 'cta_label', label: 'Knop label' },
            { type: 'text', name: 'cta_href', label: 'Knop link' },
            {
              type: 'select',
              name: 'cta_variant',
              label: 'Knop variant',
              options: [
                { value: 'accent', name: 'Accent' },
                { value: 'primary', name: 'Primair' },
                { value: 'secondary', name: 'Secundair' },
                { value: 'ghost', name: 'Ghost' },
              ],
            },
          ],
          title: 'Klaar om te beginnen?',
          text: 'Korte uitleg van de waardepropositie.',
          cta_label: 'Gratis account',
          cta_href: '/register',
          cta_variant: 'accent',
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          components: [
            { tagName: 'h2', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Klaar om te beginnen?' },
            { tagName: 'p', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: 'Korte uitleg van de waardepropositie.' },
            { type: 'nd-btn', label: 'Gratis account', href: '/register', variant: 'accent' },
          ],
        },
        init() {
          this.on('change:title', this.onTitleChange);
          this.on('change:text', this.onTextChange);
          this.on('change:cta_label', this.onCtaLabelChange);
          this.on('change:cta_href', this.onCtaHrefChange);
          this.on('change:cta_variant', this.onCtaVariantChange);
          const self = this;
          setTimeout(function () {
            self.onTitleChange();
            self.onTextChange();
            self.onCtaLabelChange();
            self.onCtaHrefChange();
            self.onCtaVariantChange();
          }, 0);
        },
        _btn() {
          // Find nested nd-btn by type attribute
          const comps = this.components();
          for (let i = 0; i < comps.length; i++) {
            const c = comps.at(i);
            if (c.get('type') === 'nd-btn') return c;
          }
          return null;
        },
        onTitleChange() {
          const el = (this.find('h2') || [])[0];
          if (el) setText(el, this.get('title') || '');
        },
        onTextChange() {
          const el = (this.find('p') || [])[0];
          if (el) setText(el, this.get('text') || '');
        },
        onCtaLabelChange() {
          const btn = this._btn();
          if (btn) btn.set('label', this.get('cta_label') || '');
        },
        onCtaHrefChange() {
          const btn = this._btn();
          if (btn) btn.set('href', this.get('cta_href') || '#');
        },
        onCtaVariantChange() {
          const btn = this._btn();
          if (btn) btn.set('variant', this.get('cta_variant') || 'accent');
        },
      },
    });

    // =========================================================================
    // Pattern 10: Simple testimonial
    // =========================================================================

    dc.addType('nd-testimonial', {
      isComponent: function (el) {
        if (!el.classList) return false;
        if (el.tagName !== 'BLOCKQUOTE') return false;
        return el.classList.contains('testimonial');
      },
      model: {
        defaults: {
          tagName: 'blockquote',
          classes: ['testimonial'],
          traits: [
            { type: 'textarea', name: 'quote', label: 'Citaat' },
            { type: 'text', name: 'cite', label: 'Bron' },
          ],
          quote: '\u201cDit spaarde me twee uur voorbereiding.\u201d',
          cite: '\u2014 Raadslid, Rotterdam',
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          components: [
            { tagName: 'p', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: '\u201cDit spaarde me twee uur voorbereiding.\u201d' },
            { tagName: 'cite', type: 'text', editable: false, removable: false, draggable: false, selectable: false, stylable: false, components: '\u2014 Raadslid, Rotterdam' },
          ],
        },
        init() {
          this.on('change:quote', this.onQuoteChange);
          this.on('change:cite', this.onCiteChange);
          const self = this;
          setTimeout(function () { self.onQuoteChange(); self.onCiteChange(); }, 0);
        },
        onQuoteChange() {
          const el = (this.find('p') || [])[0];
          if (el) setText(el, this.get('quote') || '');
        },
        onCiteChange() {
          const el = (this.find('cite') || [])[0];
          if (el) setText(el, this.get('cite') || '');
        },
      },
    });

    // =========================================================================
    // Pattern 11: Free-edit leaves
    // =========================================================================

    dc.addType('nd-text-block', {
      isComponent: function (el) {
        return !!(el.classList && el.classList.contains('text-block'));
      },
      model: {
        defaults: {
          tagName: 'div',
          classes: ['text-block'],
          draggable: true,
          removable: true,
          copyable: true,
          stylable: false,
          droppable: 'p, h2, h3',
          components: [
            { tagName: 'p', type: 'text', editable: true, stylable: false, components: 'Tekstinhoud hier...' },
          ],
        },
      },
    });

    dc.addType('nd-paragraph', {
      // No unique class — skip isComponent so plain <p> isn't hijacked on template load.
      model: {
        defaults: {
          tagName: 'p',
          draggable: true,
          removable: true,
          copyable: true,
          editable: true,
          stylable: false,
          components: 'Paragraaftekst hier...',
        },
      },
    });

    dc.addType('nd-heading-h2', {
      model: {
        defaults: {
          tagName: 'h2',
          draggable: true,
          removable: true,
          copyable: true,
          editable: true,
          stylable: false,
          components: 'Koptekst niveau 2',
        },
      },
    });

    dc.addType('nd-heading-h3', {
      model: {
        defaults: {
          tagName: 'h3',
          draggable: true,
          removable: true,
          copyable: true,
          editable: true,
          stylable: false,
          components: 'Koptekst niveau 3',
        },
      },
    });
  }

  // Export to window so editor.html can call it after grapesjs.init().
  window.registerNDComponents = registerNDComponents;
})(window);
