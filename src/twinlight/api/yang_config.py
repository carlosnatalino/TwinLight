"""Which T-API v2.6.0 data nodes are configuration, for ``content=``.

RFC 8040 §4.8.1 has ``content=config`` return "only configuration
descendant data nodes" and ``content=nonconfig`` "only non-configuration"
ones; YANG decides which is which through its ``config`` statement, and
"If a node has 'config' set to 'false', no node underneath it can have
'config' set to 'true'" (RFC 7950 §7.21.1).

``TREE`` is derived mechanically from the ``pyang -f tree`` output of the
TAPI v2.6.0 YANG modules (tapi-common, -topology, -connectivity,
-path-computation, -equipment, -oam, -streaming, -photonic-media; Linux
Foundation ONMI project, distributed under the Apache License 2.0,
https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI/tree/v2.6.0/YANG),
with every augment applied in place. It holds every node under
``/tapi-common:context``: the YANG modules themselves are not part of this
repository. Format, one data node per line, two spaces of indentation per
level:

- ``name`` a ``config true`` container, ``name [k1 k2]`` a ``config true``
  list with its keys;
- ``= a b`` its ``config true`` leaves and leaf-lists;
- ``; c d`` its ``config false`` children, of any kind. Their subtrees are
  not listed: every node in them is non-configuration.

A name is module-qualified where the module changes, as in RFC 7951 §4.
``tests/test_api/test_content_parameter.py`` fails when the twin serves a
member this table does not classify. To regenerate, run ``pyang -f tree``
on the modules and turn each ``+--rw``/``+--ro`` node into the lines above.
"""

from __future__ import annotations

TREE = """\
tapi-common:context
  = uuid
  ; service-interface-point profile
  name [value-name]
    = value-name value
  tapi-connectivity:connectivity-context
    connectivity-service [uuid]
      = layer-protocol-name layer-protocol-qualifier direction uuid administrative-state
      ; connection operational-state lifecycle-state
      end-point [local-id]
        = layer-protocol-name layer-protocol-qualifier direction role protection-role
        = local-id administrative-state
        ; csep-role connection-end-point operational-state lifecycle-state
        capacity
          total-size
            = value unit
        service-interface-point
          = service-interface-point-uuid
        peer-fwd-connectivity-service-end-point
          = connectivity-service-uuid connectivity-service-end-point-local-id
        server-connectivity-service-end-point
          = connectivity-service-uuid connectivity-service-end-point-local-id
        protecting-connectivity-service-end-point
          = connectivity-service-uuid connectivity-service-end-point-local-id
        assembled-connectivity-service-end-point [connectivity-service-uuid connectivity-service-end-point-local-id]
          = connectivity-service-uuid connectivity-service-end-point-local-id
        layer-protocol-constraint [local-id]
          = layer-protocol-name layer-protocol-qualifier local-id
          name [value-name]
            = value-name value
          tapi-photonic-media:mcg-connectivity-service-end-point-spec
            = number-of-mc
            mc-spectrum-config-pac [local-id]
              = local-id
              spectrum
                = upper-frequency lower-frequency
              edge-frequency-constraint
                = adjustment-granularity grid-type
              power-management-config-pac
                max-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                max-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
              name [value-name]
                = value-name value
            mc-bandwidth-config-pac [local-id]
              = spectrum-bandwidth local-id
              edge-frequency-constraint
                = adjustment-granularity grid-type
              power-management-config-pac
                max-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                max-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
              name [value-name]
                = value-name value
            mc-flexi-grid-config-pac [local-id]
              = local-id
              flexi-grid-config-pac
                = n m flexi-ch-spc-type flexi-slot-width-granularity
              power-management-config-pac
                max-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                max-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
              name [value-name]
                = value-name value
          tapi-photonic-media:otsia-connectivity-service-end-point-spec
            = number-of-otsi total-power-warn-threshold-upper
            = total-power-warn-threshold-lower
            otsi-config [local-id]
              = central-frequency channel-output-power laser-control local-id
              application-identifier
                = standard-application-code proprietary-application-id
              modulation
                = standard-modulation-technique proprietary-modulation-technique
              otsi-threshold-power-config
                = total-power-warn-threshold-upper total-power-warn-threshold-lower
              power-management-config-pac
                max-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                max-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
              name [value-name]
                = value-name value
          tapi-photonic-media:otsi-mcg-connectivity-service-end-point-spec
            = number-of-otsi-mc
            otsi-mc-bandwidth-config-pac [local-id]
              = spectrum-bandwidth center-frequency-offset non-adjacent-spectrum local-id
              center-frequency-constraint
                = adjustment-granularity grid-type
              edge-frequency-constraint
                = adjustment-granularity grid-type
              power-management-config-pac
                max-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                max-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
              name [value-name]
                = value-name value
            otsi-mc-spectrum-config-pac [local-id]
              = center-frequency-offset local-id
              spectrum
                = upper-frequency lower-frequency
              center-frequency-constraint
                = adjustment-granularity grid-type
              edge-frequency-constraint
                = adjustment-granularity grid-type
              power-management-config-pac
                max-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                max-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
              name [value-name]
                = value-name value
            otsi-mc-frequency-config-pac [local-id]
              = central-frequency center-frequency-offset spectrum-bandwidth local-id
              center-frequency-constraint
                = adjustment-granularity grid-type
              edge-frequency-constraint
                = adjustment-granularity grid-type
              power-management-config-pac
                max-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                max-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
              name [value-name]
                = value-name value
            otsi-mc-flexi-grid-config-pac [local-id]
              = local-id
              flexi-grid-config-pac
                = n m flexi-ch-spc-type flexi-slot-width-granularity
              power-management-config-pac
                max-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-output-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                max-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
                min-input-power
                  ; power-spectral-density
                  total-power
                    metric-values [pm-parameter-value-type]
                      = pm-parameter-value pm-parameter-unit pm-parameter-value-type
              name [value-name]
                = value-name value
        profile [profile-uuid]
          = profile-uuid
        sink-profile [profile-uuid]
          = profile-uuid
        source-profile [profile-uuid]
          = profile-uuid
        name [value-name]
          = value-name value
      connectivity-constraint
        = service-type service-level
        requested-capacity
          total-size
            = value unit
        schedule
          = start-time end-time
        coroute-inclusion
          = connectivity-service-uuid
        diversity-exclusion [connectivity-service-uuid]
          = connectivity-service-uuid
        connection-inclusion [connection-uuid]
          = connection-uuid
        connection-exclusion [connection-uuid]
          = connection-uuid
      routing-constraint
        = diversity-policy route-objective-function is-exclusive tolerable-impact
        cost-characteristic [cost-name]
          = cost-name cost-value cost-algorithm
        latency-characteristic [traffic-property-name]
          = traffic-property-name queuing-latency-characteristic
          ; fixed-latency-characteristic jitter-characteristic wander-characteristic
        risk-diversity-characteristic [risk-characteristic-name]
          = risk-characteristic-name risk-identifier-list
        max-allowed-cost
          = value priority
        max-allowed-hops
          = value priority
        max-allowed-delay
          = value priority
      topology-constraint [local-id]
        = explicit-route preferred-transport-layer constraint-weight local-id
        include-topology [topology-uuid]
          = topology-uuid
        exclude-topology [topology-uuid]
          = topology-uuid
        include-path [path-uuid]
          = path-uuid
        exclude-path [path-uuid]
          = path-uuid
        include-link [topology-uuid link-uuid]
          = topology-uuid link-uuid
        exclude-link [topology-uuid link-uuid]
          = topology-uuid link-uuid
        include-link-and-partition [index]
          = index partition-type
          link
            = topology-uuid link-uuid
        exclude-link-and-partition [index]
          = index partition-type
          link
            = topology-uuid link-uuid
        include-node [topology-uuid node-uuid]
          = topology-uuid node-uuid
        exclude-node [topology-uuid node-uuid]
          = topology-uuid node-uuid
        include-node-edge-point [topology-uuid node-uuid node-edge-point-uuid]
          = topology-uuid node-uuid node-edge-point-uuid
        exclude-node-edge-point [topology-uuid node-uuid node-edge-point-uuid]
          = topology-uuid node-uuid node-edge-point-uuid
        include-nep-and-partition [index]
          = index partition-type
          node-edge-point
            = topology-uuid node-uuid node-edge-point-uuid
        exclude-nep-and-partition [index]
          = index partition-type
          node-edge-point
            = topology-uuid node-uuid node-edge-point-uuid
        name [value-name]
          = value-name value
        connection-and-route-constraint
          include-connection-and-route [index]
            = index
            connection
              = connection-uuid
            route
              = connection-uuid
              ; route-local-id
          exclude-connection-and-route [index]
            = index
            connection
              = connection-uuid
            route
              = connection-uuid
              ; route-local-id
        connection-and-partition-constraint
          include-connection-and-partition
            = index partition-type
            connection
              = connection-uuid
          exclude-connection-and-partition
            = index partition-type
            connection
              = connection-uuid
      resilience-constraint
        = restoration-coordinate-type fault-condition-determination restore-priority
        = set-up-priority reversion-mode hold-off-time is-lock-out is-frozen
        = is-coordinated-switching-both-ends max-switch-times preferred-restoration-layer
        = selection-control
        resilience-type
          = restoration-policy protection-type
        wait-to-revert-time
          = value unit
        resiliency-route-constraint [local-id]
          = priority local-id
          routing-constraint
            = diversity-policy route-objective-function is-exclusive tolerable-impact
            cost-characteristic [cost-name]
              = cost-name cost-value cost-algorithm
            latency-characteristic [traffic-property-name]
              = traffic-property-name queuing-latency-characteristic
              ; fixed-latency-characteristic jitter-characteristic wander-characteristic
            risk-diversity-characteristic [risk-characteristic-name]
              = risk-characteristic-name risk-identifier-list
            max-allowed-cost
              = value priority
            max-allowed-hops
              = value priority
            max-allowed-delay
              = value priority
          topology-constraint
            = explicit-route preferred-transport-layer constraint-weight local-id
            include-topology [topology-uuid]
              = topology-uuid
            exclude-topology [topology-uuid]
              = topology-uuid
            include-path [path-uuid]
              = path-uuid
            exclude-path [path-uuid]
              = path-uuid
            include-link [topology-uuid link-uuid]
              = topology-uuid link-uuid
            exclude-link [topology-uuid link-uuid]
              = topology-uuid link-uuid
            include-link-and-partition [index]
              = index partition-type
              link
                = topology-uuid link-uuid
            exclude-link-and-partition [index]
              = index partition-type
              link
                = topology-uuid link-uuid
            include-node [topology-uuid node-uuid]
              = topology-uuid node-uuid
            exclude-node [topology-uuid node-uuid]
              = topology-uuid node-uuid
            include-node-edge-point [topology-uuid node-uuid node-edge-point-uuid]
              = topology-uuid node-uuid node-edge-point-uuid
            exclude-node-edge-point [topology-uuid node-uuid node-edge-point-uuid]
              = topology-uuid node-uuid node-edge-point-uuid
            include-nep-and-partition [index]
              = index partition-type
              node-edge-point
                = topology-uuid node-uuid node-edge-point-uuid
            exclude-nep-and-partition [index]
              = index partition-type
              node-edge-point
                = topology-uuid node-uuid node-edge-point-uuid
            name [value-name]
              = value-name value
            connection-and-partition-constraint
              include-connection-and-partition
                = index partition-type
                connection
                  = connection-uuid
              exclude-connection-and-partition
                = index partition-type
                connection
                  = connection-uuid
            connection-and-route-constraint
              include-connection-and-route [index]
                = index
                connection
                  = connection-uuid
                route
                  = connection-uuid
                  ; route-local-id
              exclude-connection-and-route [index]
                = index
                connection
                  = connection-uuid
                route
                  = connection-uuid
                  ; route-local-id
          name [value-name]
            = value-name value
      connectivity-service [connectivity-service-uuid]
        = connectivity-service-uuid
      internal-point [local-id]
        = layer-protocol-name layer-protocol-qualifier local-id
        ; connection-end-point node-edge-point
        name [value-name]
          = value-name value
      name [value-name]
        = value-name value
      connectivity-protection-service
        switch-operation [local-id]
          = operation-type local-id
          ; connection-end-point
          switch-control
            = connection-uuid switch-control-uuid
          switch
            = connection-uuid switch-control-uuid switch-local-id
          name [value-name]
            = value-name value
      tapi-oam:connectivity-oam-service
        oam-service [uuid]
          = layer-protocol-name layer-protocol-qualifier uuid administrative-state
          ; meg operational-state lifecycle-state
          oam-service-point [local-id]
            = is-mip layer-protocol-name layer-protocol-qualifier local-id
            = administrative-state
            ; mep mip operational-state lifecycle-state
            service-interface-point
              = service-interface-point-uuid
            connectivity-service-end-point
              = connectivity-service-uuid connectivity-service-end-point-local-id
            connection-end-point
              = topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid
            name [value-name]
              = value-name value
          name [value-name]
            = value-name value
      tapi-oam:connectivity-oam-job-service
        oam-job-service [uuid]
          = oam-job-type uuid
          schedule
            = start-time end-time
          pm-parameter-config [local-id]
            = applicable-job-type time-of-the-day-alignment is-transient layer-protocol-name
            = layer-protocol-qualifier codirectional local-id
            granularity-period
              = value unit
            pm-parameter [pm-parameter-name]
              = pm-parameter-name
              threshold-config [threshold-location threshold-type clear-threshold]
                = threshold-location threshold-type clear-threshold
                = thrs-additional-qualifier
                pm-parameter-value
                  = pm-parameter-value pm-parameter-unit pm-parameter-value-type
            name [value-name]
              = value-name value
          profile
            = profile-uuid
          oam-service
            = oam-service-uuid
          oam-service-point [local-id]
            = is-mip layer-protocol-name layer-protocol-qualifier local-id
            = administrative-state
            ; mep mip operational-state lifecycle-state
            service-interface-point
              = service-interface-point-uuid
            connectivity-service-end-point
              = connectivity-service-uuid connectivity-service-end-point-local-id
            connection-end-point
              = topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid
            name [value-name]
              = value-name value
          connection [connection-uuid]
            = connection-uuid
          connection-end-point [topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid]
            = topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid
          oam-job-descriptor
            = oam-job-descriptor-uuid
          name [value-name]
            = value-name value
    connection [uuid]
      = uuid
      ; layer-protocol-name layer-protocol-qualifier direction lower-connection
      ; server-connection supported-client-link bounding-node route
      ; connection-spec-reference operational-state lifecycle-state
      connection-end-point [topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid]
        = topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid
      switch-control [uuid]
        = uuid
        ; control-parameters
        sub-switch-control [connection-uuid switch-control-uuid]
          = connection-uuid switch-control-uuid
        switch [local-id]
          = local-id
          ; selected-connection-end-point selected-route selection-reason switch-direction
          name [value-name]
            = value-name value
        name [value-name]
          = value-name value
      name [value-name]
        = value-name value
      tapi-equipment:physical-route-list
        ; physical-route
  tapi-equipment:physical-context
    ; device physical-span
  tapi-oam:oam-context
    ; meg mep-pm-data mip-pm-data cep-pm-data fm-data-of-monitored-point
    oam-service [uuid]
      = layer-protocol-name layer-protocol-qualifier uuid administrative-state
      ; meg operational-state lifecycle-state
      oam-service-point [local-id]
        = is-mip layer-protocol-name layer-protocol-qualifier local-id administrative-state
        ; mep mip operational-state lifecycle-state
        service-interface-point
          = service-interface-point-uuid
        connectivity-service-end-point
          = connectivity-service-uuid connectivity-service-end-point-local-id
        connection-end-point
          = topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid
        name [value-name]
          = value-name value
      name [value-name]
        = value-name value
    oam-job-service [uuid]
      = oam-job-type uuid
      schedule
        = start-time end-time
      pm-parameter-config [local-id]
        = applicable-job-type time-of-the-day-alignment is-transient layer-protocol-name
        = layer-protocol-qualifier codirectional local-id
        granularity-period
          = value unit
        pm-parameter [pm-parameter-name]
          = pm-parameter-name
          threshold-config [threshold-location threshold-type clear-threshold]
            = threshold-location threshold-type clear-threshold thrs-additional-qualifier
            pm-parameter-value
              = pm-parameter-value pm-parameter-unit pm-parameter-value-type
        name [value-name]
          = value-name value
      profile
        = profile-uuid
      oam-service
        = oam-service-uuid
      oam-service-point [local-id]
        = is-mip layer-protocol-name layer-protocol-qualifier local-id administrative-state
        ; mep mip operational-state lifecycle-state
        service-interface-point
          = service-interface-point-uuid
        connectivity-service-end-point
          = connectivity-service-uuid connectivity-service-end-point-local-id
        connection-end-point
          = topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid
        name [value-name]
          = value-name value
      connection [connection-uuid]
        = connection-uuid
      connection-end-point [topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid]
        = topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid
      oam-job-descriptor
        = oam-job-descriptor-uuid
      name [value-name]
        = value-name value
    oam-job-descriptor [uuid]
      = uuid
      ; oam-job-type oam-job-state creation-time schedule results cep-pm-data mep-pm-data
      ; mip-pm-data
      name [value-name]
        = value-name value
    oam-job [uuid]
      = oam-job-type uuid administrative-state
      ; oam-job-state creation-time results current-data operational-state lifecycle-state
      schedule
        = start-time end-time
      oam-service-point [oam-service-uuid oam-service-point-local-id]
        = oam-service-uuid oam-service-point-local-id
      connection-end-point [topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid]
        = topology-uuid node-uuid node-edge-point-uuid connection-end-point-uuid
      connectivity-service-end-point
        = connectivity-service-uuid connectivity-service-end-point-local-id
      profile
        = profile-uuid
      pm-data [local-id]
        = applicable-job-type time-of-the-day-alignment is-transient codirectional local-id
        granularity-period
          = value unit
        pm-parameter [pm-parameter-name]
          = pm-parameter-name
          threshold-config [threshold-location threshold-type clear-threshold]
            = threshold-location threshold-type clear-threshold thrs-additional-qualifier
            pm-parameter-value
              = pm-parameter-value pm-parameter-unit pm-parameter-value-type
        name [value-name]
          = value-name value
      name [value-name]
        = value-name value
  tapi-path-computation:path-computation-context
    ; path
    path-comp-service [uuid]
      = layer-protocol-name direction uuid
      ; path
      end-point [local-id]
        = layer-protocol-name layer-protocol-qualifier role direction local-id
        capacity
          total-size
            = value unit
        service-interface-point
          = service-interface-point-uuid
        node-edge-point
          = topology-uuid node-uuid node-edge-point-uuid
        name [value-name]
          = value-name value
      routing-constraint
        = diversity-policy route-objective-function is-exclusive tolerable-impact
        cost-characteristic [cost-name]
          = cost-name cost-value cost-algorithm
        latency-characteristic [traffic-property-name]
          = traffic-property-name queuing-latency-characteristic
          ; fixed-latency-characteristic jitter-characteristic wander-characteristic
        risk-diversity-characteristic [risk-characteristic-name]
          = risk-characteristic-name risk-identifier-list
        max-allowed-cost
          = value priority
        max-allowed-hops
          = value priority
        max-allowed-delay
          = value priority
      topology-constraint [local-id]
        = explicit-route preferred-transport-layer constraint-weight local-id
        include-topology [topology-uuid]
          = topology-uuid
        exclude-topology [topology-uuid]
          = topology-uuid
        include-path [path-uuid]
          = path-uuid
        exclude-path [path-uuid]
          = path-uuid
        include-link [topology-uuid link-uuid]
          = topology-uuid link-uuid
        exclude-link [topology-uuid link-uuid]
          = topology-uuid link-uuid
        include-link-and-partition [index]
          = index partition-type
          link
            = topology-uuid link-uuid
          tapi-photonic-media:photonic-position
            = central-frequency
            spectrum
              = upper-frequency lower-frequency
        exclude-link-and-partition [index]
          = index partition-type
          link
            = topology-uuid link-uuid
          tapi-photonic-media:photonic-position
            = central-frequency
            spectrum
              = upper-frequency lower-frequency
        include-node [topology-uuid node-uuid]
          = topology-uuid node-uuid
        exclude-node [topology-uuid node-uuid]
          = topology-uuid node-uuid
        include-node-edge-point [topology-uuid node-uuid node-edge-point-uuid]
          = topology-uuid node-uuid node-edge-point-uuid
        exclude-node-edge-point [topology-uuid node-uuid node-edge-point-uuid]
          = topology-uuid node-uuid node-edge-point-uuid
        include-nep-and-partition [index]
          = index partition-type
          node-edge-point
            = topology-uuid node-uuid node-edge-point-uuid
          tapi-photonic-media:photonic-position
            = central-frequency
            spectrum
              = upper-frequency lower-frequency
        exclude-nep-and-partition [index]
          = index partition-type
          node-edge-point
            = topology-uuid node-uuid node-edge-point-uuid
          tapi-photonic-media:photonic-position
            = central-frequency
            spectrum
              = upper-frequency lower-frequency
        name [value-name]
          = value-name value
      objective-function
        = local-id
        ; bandwidth-optimization concurrent-paths cost-optimization link-utilization
        ; resource-sharing
        name [value-name]
          = value-name value
      optimization-constraint
        = local-id
        ; traffic-interruption
        name [value-name]
          = value-name value
      path-set [path-set-uuid]
        = path-set-uuid
      path-set-constraint
        = corouting-policy diversity-policy
        path-constraint [local-id]
          = priority local-id
          routing-constraint
            = diversity-policy route-objective-function is-exclusive tolerable-impact
            cost-characteristic [cost-name]
              = cost-name cost-value cost-algorithm
            latency-characteristic [traffic-property-name]
              = traffic-property-name queuing-latency-characteristic
              ; fixed-latency-characteristic jitter-characteristic wander-characteristic
            risk-diversity-characteristic [risk-characteristic-name]
              = risk-characteristic-name risk-identifier-list
            max-allowed-cost
              = value priority
            max-allowed-hops
              = value priority
            max-allowed-delay
              = value priority
          topology-constraint
            = explicit-route preferred-transport-layer constraint-weight local-id
            include-topology [topology-uuid]
              = topology-uuid
            exclude-topology [topology-uuid]
              = topology-uuid
            include-path [path-uuid]
              = path-uuid
            exclude-path [path-uuid]
              = path-uuid
            include-link [topology-uuid link-uuid]
              = topology-uuid link-uuid
            exclude-link [topology-uuid link-uuid]
              = topology-uuid link-uuid
            include-link-and-partition [index]
              = index partition-type
              link
                = topology-uuid link-uuid
            exclude-link-and-partition [index]
              = index partition-type
              link
                = topology-uuid link-uuid
            include-node [topology-uuid node-uuid]
              = topology-uuid node-uuid
            exclude-node [topology-uuid node-uuid]
              = topology-uuid node-uuid
            include-node-edge-point [topology-uuid node-uuid node-edge-point-uuid]
              = topology-uuid node-uuid node-edge-point-uuid
            exclude-node-edge-point [topology-uuid node-uuid node-edge-point-uuid]
              = topology-uuid node-uuid node-edge-point-uuid
            include-nep-and-partition [index]
              = index partition-type
              node-edge-point
                = topology-uuid node-uuid node-edge-point-uuid
            exclude-nep-and-partition [index]
              = index partition-type
              node-edge-point
                = topology-uuid node-uuid node-edge-point-uuid
            name [value-name]
              = value-name value
          path-optimization-constraint
            = local-id
            ; traffic-interruption
            name [value-name]
              = value-name value
          path-objective-function
            = local-id
            ; bandwidth-optimization concurrent-paths cost-optimization link-utilization
            ; resource-sharing
            name [value-name]
              = value-name value
          path-as-constraint
            include-orphan-path [path-uuid]
              = path-uuid
            exclude-path [path-uuid]
              = path-uuid
          path-service-end-point [local-id]
            = layer-protocol-name layer-protocol-qualifier role direction local-id
            capacity
              total-size
                = value unit
            service-interface-point
              = service-interface-point-uuid
            node-edge-point
              = topology-uuid node-uuid node-edge-point-uuid
            name [value-name]
              = value-name value
          name [value-name]
            = value-name value
      adopt-orphan-path
        path [path-uuid]
          = path-uuid
      name [value-name]
        = value-name value
    path-set [uuid]
      = uuid
      ; path
      name [value-name]
        = value-name value
  tapi-streaming:stream-admin-context
    = uuid
    ; stream-monitor
    name [value-name]
      = value-name value
  tapi-streaming:stream-context
    ; available-stream supported-stream-type
  tapi-topology:topology-context
    ; nw-topology-service
    topology [uuid]
      = uuid
      ; layer-protocol-name node boundary-node-edge-point
      link [uuid]
        = uuid administrative-state error-characteristic loss-characteristic
        = repeat-delivery-characteristic delivery-order-characteristic
        = unavailable-time-characteristic server-integrity-process-characteristic
        ; layer-protocol-name direction resilience-type node-edge-point operational-state
        ; lifecycle-state total-potential-capacity available-capacity validation-mechanism
        ; transitioned-layer-protocol-name
        name [value-name]
          = value-name value
        cost-characteristic [cost-name]
          = cost-name cost-value cost-algorithm
        latency-characteristic [traffic-property-name]
          = traffic-property-name queuing-latency-characteristic
          ; fixed-latency-characteristic jitter-characteristic wander-characteristic
        risk-characteristic [risk-characteristic-name]
          = risk-characteristic-name risk-identifier-list
        tapi-equipment:supporting-physical-span
          ; physical-span
      name [value-name]
        = value-name value
"""
