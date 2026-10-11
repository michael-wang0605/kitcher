from django import template

from pantry.services.quantities import display

register = template.Library()
register.filter("amount", display)
