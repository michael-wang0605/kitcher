"use strict";
const addIngredient = document.getElementById("add-ingredient");
if (addIngredient) {
  addIngredient.addEventListener("click", () => {
    const total = document.getElementById("id_ingredients-TOTAL_FORMS");
    const index = Number(total.value);
    if (index >= 100) {
      document.getElementById("ingredient-limit").textContent = "This recipe supports up to 100 ingredient lines.";
      return;
    }
    const html = document.getElementById("ingredient-empty").innerHTML.replaceAll("__prefix__", String(index));
    document.getElementById("recipe-lines").insertAdjacentHTML("beforeend", html);
    total.value = String(index + 1);
  });
}