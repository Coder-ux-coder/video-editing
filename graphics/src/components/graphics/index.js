// Template registry: template component name -> React component. Python template specs
// (templates/*.json) name one of these as "component".
import {CTA, Handle, LogoBug, ProgressBar} from './Brand.jsx';
import {Callout} from './Callout.jsx';
import {Chart, Compare, Counter} from './Data.jsx';
import {KineticText} from './Kinetic.jsx';
import {ListReveal} from './Lists.jsx';
import {LowerThird} from './LowerThird.jsx';
import {TitleCard} from './TitleCard.jsx';
import {Transition} from './Transition.jsx';

export const COMPONENTS = {LowerThird, TitleCard, Callout, ListReveal, KineticText, Counter, Chart, Compare,
	CTA, ProgressBar, LogoBug, Handle, Transition};
