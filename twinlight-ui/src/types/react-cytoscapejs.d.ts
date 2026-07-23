declare module "react-cytoscapejs" {
  import type { Component } from "react";
  import type cytoscape from "cytoscape";

  export interface CytoscapeComponentProps {
    elements: cytoscape.ElementDefinition[];
    stylesheet?: Array<cytoscape.StylesheetStyle | cytoscape.StylesheetCSS>;
    layout?: cytoscape.LayoutOptions;
    style?: React.CSSProperties;
    className?: string;
    id?: string;
    cy?: (cy: cytoscape.Core) => void;
    [key: string]: unknown;
  }

  export default class CytoscapeComponent extends Component<CytoscapeComponentProps> {}
}
