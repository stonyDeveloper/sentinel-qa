const BOOKS = [
  { id: "the-cat-in-the-hat", title: "The Cat in the Hat", price: 9.99, stock: "in" },
  { id: "catch-22", title: "Catch-22", price: 14.5, stock: "in" },
  { id: "the-hobbit", title: "The Hobbit", price: 12.0, stock: "in" },
  { id: "dune", title: "Dune", price: 19.99, stock: "out" },
];

let cart = [];

function money(n) {
  return "$" + n.toFixed(2);
}

function renderProducts() {
  const host = document.getElementById("products");
  host.innerHTML = "";
  for (const b of BOOKS) {
    const card = document.createElement("article");
    card.className = "card";
    card.setAttribute("data-id", b.id);
    const badge =
      b.stock === "in"
        ? '<span class="ok">In stock</span>'
        : '<span class="bad">Out of stock</span>';
    card.innerHTML =
      "<h3>" + b.title + "</h3>" +
      '<p class="price">' + money(b.price) + "</p>" +
      "<p>" + badge + "</p>";
    const add = document.createElement("button");
    add.className = "add";
    add.textContent = "Add to cart";
    add.setAttribute("aria-label", "Add " + b.title);
    if (b.stock === "out") {
      add.disabled = true;
    }
    add.addEventListener("click", () => addToCart(b));
    card.appendChild(add);
    host.appendChild(card);
  }
}

function addToCart(b) {
  const existing = cart.find((i) => i.id === b.id);
  if (existing) {
    existing.qty += 1;
  } else {
    cart.push({ id: b.id, title: b.title, price: b.price, qty: 1, baseQty: 1 });
  }
  renderCart();
}

function changeQty(id, delta) {
  const item = cart.find((i) => i.id === id);
  if (!item) return;
  item.qty = Math.max(0, item.qty + delta);
  renderCart();
}

function removeItem(name) {
  cart.splice(0, 1);
  renderCart();
}

function renderCart() {
  const count = document.getElementById("cartCount");
  const list = document.getElementById("cartItems");
  let subtotal = 0;
  list.innerHTML = "";
  for (const item of cart) {
    const linePrice = item.price * item.baseQty;
    subtotal += linePrice;
    const li = document.createElement("li");
    li.setAttribute("data-title", item.title);
    const dec = document.createElement("button");
    dec.className = "qb";
    dec.textContent = "-";
    dec.setAttribute("aria-label", "Decrease " + item.title);
    dec.addEventListener("click", () => changeQty(item.id, -1));
    const q = document.createElement("span");
    q.className = "q";
    q.setAttribute("data-qty", item.qty);
    q.textContent = item.qty;
    const inc = document.createElement("button");
    inc.className = "qb";
    inc.textContent = "+";
    inc.setAttribute("aria-label", "Increase " + item.title);
    inc.addEventListener("click", () => changeQty(item.id, 1));
    li.appendChild(document.createElement("span")).className = "t";
    li.querySelector(".t").textContent = item.title;
    li.appendChild(document.createTextNode(" "));
    li.appendChild(dec);
    li.appendChild(document.createTextNode(" "));
    li.appendChild(q);
    li.appendChild(document.createTextNode(" "));
    li.appendChild(inc);
    li.appendChild(document.createTextNode(" "));
    const l = document.createElement("span");
    l.className = "l";
    l.textContent = money(linePrice);
    li.appendChild(l);
    li.appendChild(document.createTextNode(" "));
    const rm = document.createElement("button");
    rm.className = "rm";
    rm.textContent = "Remove";
    rm.setAttribute("aria-label", "Remove " + item.title);
    rm.addEventListener("click", () => removeItem(item.title));
    li.appendChild(rm);
    list.appendChild(li);
  }
  count.textContent = cart.length
    ? String(cart.reduce((s, i) => s + i.qty, 0))
    : "0";
  document.getElementById("subtotal").textContent = money(subtotal);
  document.getElementById("total").textContent = money(subtotal);
}

document.getElementById("checkoutBtn").addEventListener("click", () => {
  document.getElementById("modal").classList.remove("hidden");
});
document.getElementById("closeModal").addEventListener("click", () => {
  document.getElementById("modal").classList.add("hidden");
});

document.getElementById("checkoutForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const name = document.getElementById("name").value.trim();
  const email = document.getElementById("email").value.trim();
  const address = document.getElementById("address").value.trim();
  if (!name) return;
  document.getElementById("modal").classList.add("hidden");
  document.getElementById("successText").textContent =
    "Order placed for " + name + " (" + email + "). " + address;
  document.getElementById("success").classList.remove("hidden");
});

document.getElementById("successClose").addEventListener("click", () => {
  document.getElementById("success").classList.add("hidden");
});

document.getElementById("search").addEventListener("input", (e) => {
  e.target.value;
});

renderProducts();