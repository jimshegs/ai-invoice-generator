/*****************  Line-item helpers  *****************/
function addLineRow(item = {description:'', quantity:1, unit_price:0}) {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input class="desc" value="${item.description}"></td>
    <td><input class="qty"  type="number" min="1" value="${item.quantity}"></td>
    <td><input class="unit" type="number" min="0" value="${item.unit_price}"></td>
    <td><input class="total" readonly></td>
    <td><button class="del">🗑️</button></td>
  `;
  document.querySelector('#lineItemsTable tbody').appendChild(tr);
  tr.querySelector('.del').onclick  = ()=>{ tr.remove(); recalcTotals(); };
  ['input','change'].forEach(ev => tr.addEventListener(ev, recalcTotals));
  recalcTotals();
}

function recalcTotals(){
  let subtotal = 0;
  document.querySelectorAll('#lineItemsTable tbody tr').forEach(tr=>{
    const qty  = +tr.querySelector('.qty').value  || 0;
    const unit = +tr.querySelector('.unit').value || 0;
    const line = qty * unit;
    tr.querySelector('.total').value = line.toFixed(2);
    subtotal += line;
  });
  document.getElementById('subtotal').value   = subtotal.toFixed(2);
  const rate = (+document.getElementById('taxRate').value||0)/100;
  const tax  = subtotal * rate;
  document.getElementById('taxAmount').value  = tax.toFixed(2);
  document.getElementById('grandTotal').value = (subtotal + tax).toFixed(2);
  const paid = +document.getElementById('amountPaid').value || 0;
  const balance = (subtotal + tax - paid);
  document.getElementById('balanceDue').value = balance.toFixed(2);

// also re-run when user edits Amount Paid:
document.getElementById('amountPaid').oninput = recalcTotals;

}
document.getElementById('addLineBtn').onclick = ()=> addLineRow();
document.getElementById('taxRate').oninput = recalcTotals;

/*****************  Populate from /parse  *****************/
function preloadLineItems(data){
  // Clear table
  document.querySelector('#lineItemsTable tbody').innerHTML = '';
  if (data.line_items && data.line_items.length){
      data.line_items.forEach(addLineRow);
  } else {
      addLineRow();   // start with one blank row
  }
  if (data.tax_rate !== undefined) {
      document.getElementById('taxRate').value = data.tax_rate * 100;
  }
  recalcTotals();
}
