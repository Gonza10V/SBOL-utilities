from typing import Dict, Iterable, List, Union, Set, Optional, Tuple

import sbol3
import tyto

from sbol_utilities.helper_functions import id_sort
from sbol_utilities.workarounds import get_parent


# TODO: consider allowing return of LocalSubComponent and ExternallyDefined
def contained_components(roots: Union[sbol3.TopLevel, Iterable[sbol3.TopLevel]]) -> Set[sbol3.Component]:
    """Find the set of all SBOL Components contained within the roots or their children
    This will explore via Collection.member relations and Component.feature relations

    :param roots: single TopLevel or iterable collection of TopLevel objects to explore
    :return: set of Components found, including roots
    """
    if isinstance(roots, sbol3.TopLevel):
        roots = [roots]
    explored = set()  # set being built via traversal

    # subfunction for walking containment tree
    def walk_tree(obj: sbol3.TopLevel):
        if obj not in explored:
            explored.add(obj)
            if isinstance(obj, sbol3.Component):
                for f in (f.instance_of.lookup() for f in obj.features if isinstance(f, sbol3.SubComponent)):
                    walk_tree(f)
            elif isinstance(obj, sbol3.Collection):
                for m in obj.members:
                    walk_tree(m.lookup())
    for r in roots:
        walk_tree(r)
    # filter result for containers:
    return {c for c in explored if isinstance(c, sbol3.Component)}


def ensure_singleton_feature(system: sbol3.Component, target: Union[sbol3.Feature, sbol3.Component]):
    """Return a feature associated with the target, i.e., the target itself if a feature, or a SubComponent
    If the target is not already in the system, add it.
    Raises ValueError if given a Component with multiple instances

    :return: associated feature
    """
    if isinstance(target, sbol3.Feature):  # features are returned directly
        if target not in system.features:
            system.features.append(target)
        return target
    instances = [f for f in system.features if isinstance(f, sbol3.SubComponent) and f.instance_of == target.identity]
    if len(instances) == 1:  # if there is precisely one SubComponent, return it
        return instances[0]
    elif not len(instances):  # if there are no SubComponents, add one
        return add_feature(system, target)
    else:  # if there are multiple SubComponents, raise an exception
        raise ValueError(f'Ambiguous reference: {len(instances)} instances of {target.identity} in {system.identity}')


def ensure_singleton_system(system: Optional[sbol3.Component], *features: Union[sbol3.Feature, sbol3.Component])\
        -> sbol3.Component:
    """Check that the system referred to is unambiguous. Raises ValueError if there are multiple or zero systems

    :param system: Optional explicit specification of system
    :param features: features in the same system or components to be referenced from it
    :return: Component for the identified system
    """
    systems = set(filter(None,(get_parent(f) for f in features if isinstance(f, sbol3.Feature))))
    if system:
        systems |= {system}
    if len(systems) == 1:
        system = systems.pop()
        if not isinstance(system, sbol3.Component):
            raise ValueError(f'Could not find system, instead found {system}')
        return system
    elif not systems:
        raise ValueError(f'Could not find system: no features in {features}')
    else:
        raise ValueError(f'Multiple systems referred to: {systems}')


def add_feature(component: sbol3.Component, to_add: Union[sbol3.Feature, sbol3.Component]) -> sbol3.Feature:
    """Pass-through adder for adding a Feature to a Component for allowing slightly more compact code.
    Note that unlike ensure_singleton_feature, this allows adding multiple instances

    :param component: Component to add the Feature to
    :param to_add: Feature or Component to be added to system. Components will be wrapped in a SubComponent Feature
    :return: feature added (SubComponent if to_add was a Component)
    """
    if isinstance(to_add, sbol3.Component):
        to_add = sbol3.SubComponent(to_add)
    component.features.append(to_add)
    return to_add


def contains(container: Union[sbol3.Feature, sbol3.Component], contained: Union[sbol3.Feature, sbol3.Component],
             system: Optional[sbol3.Component] = None) -> sbol3.Feature:
    """Assert a topological containment constraint between two features (e.g., a promoter contained in a plasmid)
    Implicitly identifies system and creates/adds features as necessary

    :param container: containing feature
    :param contained: feature that is contained
    :param system: optional explicit statement of system
    :return: contained feature
    """
    # transform implicit arguments into explicit
    system = ensure_singleton_system(system, container, contained)
    container = ensure_singleton_feature(system, container)
    contained = ensure_singleton_feature(system, contained)
    # add a containment relation
    system.constraints.append(sbol3.Constraint(sbol3.SBOL_CONTAINS, subject=container, object=contained))
    return contained


def order(five_prime: Union[sbol3.Feature, sbol3.Component], three_prime: Union[sbol3.Feature, sbol3.Component],
          system: Optional[sbol3.Component] = None) -> sbol3.Feature:
    """Assert a topological ordering constraint between two features (e.g., a CDS followed by a terminator)
    Implicitly identifies system and creates/adds features as necessary

    :param five_prime: containing feature
    :param three_prime: feature that is contained
    :param system: optional explicit statement of system
    :return: three_prime feature
    """
    # transform implicit arguments into explicit
    system = ensure_singleton_system(system, five_prime, three_prime)
    five_prime = ensure_singleton_feature(system, five_prime)
    three_prime = ensure_singleton_feature(system, three_prime)
    # add a containment relation
    system.constraints.append(sbol3.Constraint(sbol3.SBOL_MEETS, subject=five_prime, object=three_prime))
    return three_prime


def regulate(five_prime: Union[sbol3.Feature, sbol3.Component], target: Union[sbol3.Feature, sbol3.Component],
             system: Optional[sbol3.Component] = None) -> sbol3.Feature:
    """Connect a 5' regulatory region to control the expression of a 3' target region
    Note: this function is an alias for "order"

    :param five_prime: Regulatory region to place upstream of target
    :param target: region to be regulated (e.g., a CDS or ncRNA)
    :param system: optional explicit statement of system
    :return: target feature
    """
    return order(five_prime, target, system)


def constitutive(target: Union[sbol3.Feature, sbol3.Component], system: Optional[sbol3.Component] = None)\
        -> sbol3.Feature:
    """Add a constitutive promoter regulating the target feature

    :param target: 5' region for promoter to regulate
    :param system: optional explicit statement of system
    :return: newly created constitutive promoter
    """
    # transform implicit arguments into explicit
    system = ensure_singleton_system(system, target)
    target = ensure_singleton_feature(system, target)

    # create a constitutive promoter and use it to regulate the target
    promoter = add_feature(system, sbol3.LocalSubComponent([sbol3.SBO_DNA], roles=[tyto.SO.constitutive_promoter]))
    regulate(promoter, target)

    # also add the promoter into any containers that hold the target
    # TODO: add lookups for constraints like we have for interactions
    containers = [c.subject for c in system.constraints
                  if c.restriction == sbol3.SBOL_CONTAINS and c.object == target.identity]
    for c in containers:
        contains(c.lookup(), promoter)

    return promoter


def add_interaction(interaction_type: str,
                    participants: Dict[Union[sbol3.Feature, sbol3.Component], str],
                    system: sbol3.Component = None,
                    name: str = None) -> sbol3.Interaction:
    """Compact function for creation of an interaction
    Implicitly identifies system and creates/adds features as necessary

    :param interaction_type: SBO type of interaction to be to be added
    :param participants: dictionary assigning features/components to roles for participations
    :param system: system to add interaction to
    :param name: name for the interaction
    :return: interaction
    """
    # transform implicit arguments into explicit
    system = ensure_singleton_system(system, *participants.keys())
    participations = [sbol3.Participation([r], ensure_singleton_feature(system, p)) for p, r in participants.items()]
    # make and return interaction
    interaction = sbol3.Interaction([interaction_type], participations=participations, name=name)
    system.interactions.append(interaction)
    return interaction


def in_role(interaction: sbol3.Interaction, role: str) -> sbol3.Feature:
    """Find the (precisely one) feature with a given role in the interaction

    :param interaction: interaction to search
    :param role: role to search for
    :return Feature playing that role
    """
    feature_participation = [p for p in interaction.participations if role in p.roles]
    if len(feature_participation) != 1:
        raise ValueError(f'Role can be in 1 participant: found {len(feature_participation)} in {interaction.identity}')
    return feature_participation[0].participant.lookup()


def all_in_role(interaction: sbol3.Interaction, role: str) -> List[sbol3.Feature]:
    """Find the features with a given role in the interaction

    :param interaction: interaction to search
    :param role: role to search for
    :return sorted list of Features playing that role
    """
    return id_sort([p.participant.lookup() for p in interaction.participations if role in p.roles])

def dna_component_with_sequence(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a DNA Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The DNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    comp_seq = sbol3.Sequence(f'{identity}_seq',
                                elements=sequence,
                                encoding=sbol3.IUPAC_DNA_ENCODING)

    dna_comp = sbol3.Component(identity, sbol3.SBO_DNA,
                                sequences =[comp_seq],
                                **kwargs)
    if dna_comp.name == None:
        dna_comp.name = identity
    return tuple([dna_comp, comp_seq])

def rna_component_with_sequence(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a RNA Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The RNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    comp_seq = sbol3.Sequence(f'{identity}_seq',
                                elements=sequence,
                                encoding=sbol3.IUPAC_RNA_ENCODING)

    rna_comp = sbol3.Component(identity, sbol3.SBO_RNA,
                                sequences =[comp_seq],
                                **kwargs)
    return tuple([rna_comp, comp_seq])

def protein_component_with_sequence(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a Protein Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The Protein sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    comp_seq = sbol3.Sequence(f'{identity}_seq',
                                elements=sequence,
                                encoding=sbol3.IUPAC_PROTEIN_ENCODING)

    pro_comp = sbol3.Component(identity, sbol3.SBO_PROTEIN,
                                sequences =[comp_seq],
                                **kwargs)
    return tuple([pro_comp, comp_seq])

def functional_component(identity: str, **kwargs)-> sbol3.Component:
    """Creates a Component of type functional entity.

    :param identity: The identity of the Component.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    fun_comp =  sbol3.Component(identity, sbol3.SBO_FUNCTIONAL_ENTITY, **kwargs)
    return fun_comp

def promoter(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a Promoter Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The DNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    promoter, promoter_seq = dna_component_with_sequence(identity, sequence, **kwargs)
    promoter.roles.append(sbol3.SO_PROMOTER)
    return tuple([promoter, promoter_seq])

def rbs(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a Ribosome Entry Site (RBS) Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The DNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    rbs, rbs_seq = dna_component_with_sequence(identity, sequence, **kwargs)
    rbs.roles. append(sbol3.SO_RBS)
    return tuple([rbs, rbs_seq])

def cds(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a Coding Sequence (CDS) Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The DNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    cds, cds_seq = dna_component_with_sequence(identity, sequence, **kwargs)
    cds.roles. append(sbol3.SO_CDS)
    return tuple([cds, cds_seq])

def terminator(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a Terminator Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The DNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    terminator, terminator_seq = dna_component_with_sequence(identity, sequence, **kwargs)
    terminator.roles. append(sbol3.SO_CDS)
    return tuple([terminator, terminator_seq])

def protein_stability_element(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a protein stability element Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The DNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    protein_stability_element, protein_stability_element_seq = dna_component_with_sequence(identity, sequence, **kwargs)
    protein_stability_element.roles. append(tyto.SO.protein_stability_element)
    return tuple([protein_stability_element, protein_stability_element_seq])

def gene(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a Gene Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The DNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    gene, gene_seq = dna_component_with_sequence(identity, sequence, **kwargs)
    gene.roles. append(sbol3.SO_GENE)
    return tuple([gene, gene_seq])

def operator(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates an Operator Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The DNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    operator, operator_seq = dna_component_with_sequence(identity, sequence, **kwargs)
    operator.roles. append(sbol3.SO_OPERATOR)
    return tuple([operator, operator_seq])

def engineered_region(identity: str, features: Union[List[sbol3.SubComponent],List[sbol3.Component]], **kwargs)-> sbol3.Component:
    """Creates an Engineered Region Component.

    :param identity: The identity of the Component.
    :param sub components: SubComponents or Components to add as features.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    engineered_region = sbol3.Component(identity, sbol3.SBO_DNA, **kwargs)
    engineered_region.roles.append(sbol3.SO_ENGINEERED_REGION)
    for to_add in features:
        if isinstance(to_add, sbol3.Component):
            to_add = sbol3.SubComponent(to_add)
        engineered_region.features.append(to_add)
    if len(engineered_region.features) > 1:
            for i in range(len(engineered_region.features)-1):
                engineered_region.constraints = [sbol3.Constraint(sbol3.SBOL_PRECEDES, engineered_region.features[i], engineered_region.features[i+1])]
    else: pass
    return engineered_region

def mrna(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates an mRNA Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The RNA sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    mrna, mrna_seq = rna_component_with_sequence(identity, sequence, **kwargs)
    mrna.roles. append(sbol3.SO_MRNA)
    return tuple([mrna, mrna_seq])

def transcription_factor(identity: str, sequence: str, **kwargs)-> Tuple[sbol3.Component, sbol3.Sequence]:
    """Creates a Trancription Factor Component and its Sequence.

    :param identity: The identity of the Component. The identity of Sequence is also identity with the suffix '_seq'.
    :param sequence: The Protein amino acid sequence of the Component encoded in IUPAC.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A tuple of Component and Sequence.
    """
    transcription_factor, transcription_factor_seq = protein_component_with_sequence(identity, sequence, **kwargs)
    transcription_factor.roles. append(sbol3.SO_TRANSCRIPTION_FACTOR)
    return tuple([transcription_factor, transcription_factor_seq])


def media(identity: str, recipe = None, **kwargs)-> sbol3.Component:
    """Creates a media Component of type functional entity.

    :param identity: The identity of the Component.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A Component object.
    """
    media =  sbol3.Component(identity, sbol3.SBO_FUNCTIONAL_ENTITY, **kwargs)
    media.roles.append(tyto.NCIT.Media)
    if recipe:
        for key, value in recipe.items():
            if isinstance(key, sbol3.Component):
                key = sbol3.SubComponent(key)
            key.measures.append(sbol3.Measure(value[0], value[1]))
            media.features.append(key)
    return media

def strain(identity: str, **kwargs)-> sbol3.Component:
    """Creates a strain Component of type functional entity.

    :param identity: The identity of the Component.
    :param **kwargs: Keyword arguments of any other Component attribute.
    :return: A Component object.
    """
    strain =  sbol3.Component(identity, sbol3.SBO_FUNCTIONAL_ENTITY, **kwargs)
    strain.roles.append(tyto.NCIT.Strain)
    return strain

def ed_simple_chemical(definition: str, **kwargs)-> sbol3.Component:
    """Creates an ExternallyDefined Simple Chemical Component.

    :param definition: The URI that links to a canonical definitino external to SBOL, recommended ChEBI.
    :param **kwargs: Keyword arguments of any other ExternallyDefined attribute.
    :return: A Component object.
    """
    ed_simple_chemical = sbol3.ExternallyDefined([sbol3.SBO_SIMPLE_CHEMICAL], definition, **kwargs)
    return ed_simple_chemical

def ed_protein(definition: str, **kwargs)-> sbol3.Component:
    """Creates an ExternallyDefined Protein Component.

    :param definition: The URI that links to a canonical definitino external to SBOL, recommended UniProt.
    :param **kwargs: Keyword arguments of any other ExternallyDefined attribute.
    :return: A Component object.
    """
    ed_protein = sbol3.ExternallyDefined([sbol3.SBO_PROTEIN], definition, **kwargs)
    return ed_protein